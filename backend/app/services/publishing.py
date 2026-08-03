"""Production-safe official publishing orchestration.

This service owns tenant, approval, idempotency, and state-transition checks.
Platform integrations only perform transport work and never decide whether an
external action is allowed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.workspace_access import require_account_access
from app.integrations.douyin import (
    DouyinIntegrationError,
    build_douyin_h5_publish_schema,
    create_douyin_share_id,
    get_douyin_client_token,
    get_douyin_open_ticket,
    resolve_secret_ref,
)
from app.models import (
    Account,
    AccountClient,
    AgentToolCall,
    ContentItem,
    Deliverable,
    Event,
    MaterialAsset,
    PlatformAccountAuth,
    PlatformContentRecord,
    PlatformIntegration,
    Project,
    ProjectAccount,
    User,
)
from app.models.enums import (
    AccountStatus,
    ContentIdentityConfidence,
    DataSourceKind,
    DeliverableStatus,
    MaterialStatus,
    Platform,
    WorkspaceRole,
)
from app.models.publishing import PlatformPublishJob, PlatformPublishJobStatus
from app.schemas.orchestrator import PublishPackageOut
from app.schemas.publishing import (
    CreatePublishJobRequest,
    DouyinCreateVideoCallback,
    PublishHandoffOut,
    PublishJobOut,
)


class PublishingServiceError(RuntimeError):
    """Stable, user-safe business error raised at the publishing boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int = 409,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.details = details or {}


_CONNECTION_ERROR_CODES = frozenset(
    {
        "DOUYIN_H5_PUBLISH_DISABLED",
        "DOUYIN_APP_NOT_CONFIGURED",
        "DOUYIN_ACCOUNT_NOT_AUTHORIZED",
        "DOUYIN_PUBLISH_SCOPE_MISSING",
    }
)


async def publish_approved_artifact(
    session: AsyncSession,
    user: User,
    *,
    account_id: int,
    artifact_id: int,
    artifact_version: int,
    scheduled_at: datetime | None,
    visibility: str,
    allow_comment: bool,
) -> dict[str, Any]:
    """Create or replay an official publish handoff from one approved version.

    Approval is bound to the immutable Deliverable version.  If a newer
    version exists, callers must approve that version before any external
    action can run.
    """

    await require_account_access(
        session,
        user,
        account_id,
        roles={WorkspaceRole.LEAD, WorkspaceRole.OPERATOR},
    )
    artifact = await session.scalar(
        select(Deliverable)
        .join(ContentItem, ContentItem.id == Deliverable.content_item_id)
        .where(
            Deliverable.id == artifact_id,
            Deliverable.version == artifact_version,
            ContentItem.account_id == account_id,
        )
    )
    if artifact is None:
        raise PublishingServiceError(
            "PUBLISH_ARTIFACT_NOT_FOUND",
            "找不到当前账号对应的发布成果版本。",
            status_code=404,
        )
    if artifact.status is not DeliverableStatus.APPROVED:
        raise PublishingServiceError(
            "PUBLISH_ARTIFACT_NOT_APPROVED",
            "发布包尚未通过人工审批。",
            status_code=422,
        )
    newer_version = await session.scalar(
        select(Deliverable.id).where(
            Deliverable.content_item_id == artifact.content_item_id,
            Deliverable.type == artifact.type,
            Deliverable.version > artifact.version,
        )
    )
    if newer_version is not None:
        raise PublishingServiceError(
            "PUBLISH_APPROVAL_VERSION_STALE",
            "发布包已有新版本，旧版本审批已失效。",
            status_code=409,
        )

    payload = dict(artifact.payload or {})
    package_payload = payload.get("publish_package")
    tool_call_id = payload.get("approval_tool_call_id")
    if not isinstance(package_payload, dict) or not isinstance(tool_call_id, int):
        raise PublishingServiceError(
            "PUBLISH_ARTIFACT_CONTRACT_INVALID",
            "已审批成果缺少可执行发布包或审批来源。",
            status_code=422,
        )
    package = PublishPackageOut.model_validate(package_payload)
    requested_schedule = scheduled_at.isoformat() if scheduled_at else None
    approved_schedule = package.scheduled_at.isoformat() if package.scheduled_at else None
    if (
        approved_schedule != requested_schedule
        or package.visibility != visibility
        or package.allow_comment != allow_comment
    ):
        raise PublishingServiceError(
            "PUBLISH_APPROVAL_PACKAGE_MISMATCH",
            "发布时间或可见性设置已变化，请重新生成并审批发布包。",
            status_code=409,
        )

    digest = hashlib.sha256(
        _canonical(
            {
                "artifact_id": artifact.id,
                "version": artifact.version,
                "scheduled_at": requested_schedule,
                "visibility": visibility,
                "allow_comment": allow_comment,
            }
        ).encode("utf-8")
    ).hexdigest()
    job = await create_publish_job(
        session,
        user,
        CreatePublishJobRequest(
            account_id=account_id,
            active_project_id=None,
            active_client_id=None,
            tool_call_id=tool_call_id,
            idempotency_key=f"approved-artifact:{digest}",
            publish_package=package,
        ),
    )

    if job.status in {
        PlatformPublishJobStatus.HANDOFF_READY,
        PlatformPublishJobStatus.USER_PUBLISHING,
        PlatformPublishJobStatus.WAITING_BIND,
        PlatformPublishJobStatus.BOUND,
        PlatformPublishJobStatus.OBSERVING,
        PlatformPublishJobStatus.COMPLETED,
    }:
        return _publish_artifact_receipt(job, artifact)

    try:
        handoff = await prepare_douyin_handoff(session, user, job.id)
    except PublishingServiceError as exc:
        if exc.code not in _CONNECTION_ERROR_CODES:
            raise
        return {
            "account_id": account_id,
            "source_artifact_id": artifact.id,
            "source_artifact_version": artifact.version,
            "platform_receipt_id": None,
            "status": "blocked",
            "published_at": None,
            "retryable": exc.retryable,
            "connection_state": "needs_connection",
            "reason": exc.code,
        }
    return _publish_artifact_receipt(handoff.job, artifact)


def _publish_artifact_receipt(
    job: PlatformPublishJob | PublishJobOut,
    artifact: Deliverable,
) -> dict[str, Any]:
    status_value = (
        job.status.value
        if isinstance(job.status, PlatformPublishJobStatus)
        else str(job.status)
    )
    if status_value in {"bound", "observing", "completed"}:
        status = "published"
    elif status_value in {"waiting_bind", "user_publishing"}:
        status = "waiting_platform_confirmation"
    else:
        status = "handoff_ready"
    return {
        "account_id": job.account_id,
        "source_artifact_id": artifact.id,
        "source_artifact_version": artifact.version,
        "platform_receipt_id": job.id,
        "status": status,
        "published_at": job.bound_at if status == "published" else None,
        "retryable": False,
        "connection_state": "connected",
        "reason": None,
    }


async def create_publish_job(
    session: AsyncSession,
    user: User,
    request: CreatePublishJobRequest,
) -> PlatformPublishJob:
    """Create one immutable, idempotent publish-job execution context."""
    existing = await session.scalar(
        select(PlatformPublishJob).where(
            PlatformPublishJob.org_id == user.org_id,
            PlatformPublishJob.idempotency_key == request.idempotency_key,
        )
    )
    if existing is not None:
        _assert_idempotent_request(existing, request)
        return existing

    account = await require_account_access(
        session,
        user,
        request.account_id,
        roles={WorkspaceRole.LEAD, WorkspaceRole.OPERATOR},
    )
    if account.status != AccountStatus.ACTIVE:
        raise PublishingServiceError(
            "PUBLISH_ACCOUNT_INACTIVE",
            "当前账号不可用于发布。",
            status_code=422,
        )
    if account.platform != Platform.DOUYIN:
        raise PublishingServiceError(
            "PUBLISH_PLATFORM_UNSUPPORTED",
            "当前阶段仅支持抖音官方投稿。",
            status_code=422,
        )
    if request.publish_package.account_id != account.id:
        raise PublishingServiceError(
            "PUBLISH_PACKAGE_ACCOUNT_MISMATCH",
            "发布包与所选账号不一致。",
            status_code=422,
        )
    if request.publish_package.platform != account.platform:
        raise PublishingServiceError(
            "PUBLISH_PACKAGE_PLATFORM_MISMATCH",
            "发布包平台与所选账号不一致。",
            status_code=422,
        )

    await _validate_frozen_workspace_context(
        session,
        account=account,
        active_client_id=request.active_client_id,
        active_project_id=request.active_project_id,
    )
    tool_call = await session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.id == request.tool_call_id,
            AgentToolCall.org_id == user.org_id,
        )
    )
    if tool_call is None or tool_call.tool_code != "publish_package_prepare":
        raise PublishingServiceError(
            "PUBLISH_APPROVAL_SOURCE_INVALID",
            "找不到与该发布包对应的人工审批记录。",
            status_code=422,
        )
    _assert_approved_package_matches(tool_call, request.publish_package.model_dump(mode="json"))

    approved = _tool_call_is_approved(tool_call)
    job = PlatformPublishJob(
        org_id=user.org_id,
        account_id=account.id,
        active_client_id=request.active_client_id,
        active_project_id=request.active_project_id,
        created_by_id=user.id,
        brain_task_id=tool_call.task_id,
        tool_call_id=tool_call.id,
        platform=account.platform,
        status=(
            PlatformPublishJobStatus.TASK_CREATED
            if approved
            else PlatformPublishJobStatus.PENDING_APPROVAL
        ),
        idempotency_key=request.idempotency_key,
        publish_package=request.publish_package.model_dump(mode="json"),
        capabilities_snapshot={},
        approval_snapshot=_approval_snapshot(tool_call),
    )
    session.add(job)
    await session.flush()
    _audit_transition(
        session,
        job,
        from_status=None,
        to_status=job.status,
        reason="publish_job_created",
    )
    await session.commit()
    await session.refresh(job)
    return job


async def get_publish_job(
    session: AsyncSession,
    user: User,
    job_id: int,
) -> PlatformPublishJob:
    """Return one tenant- and account-scoped publishing job."""
    return await _get_job_for_user(session, user, job_id, write=False)


async def list_publish_jobs(
    session: AsyncSession,
    user: User,
    *,
    account_id: int | None = None,
    limit: int = 50,
) -> list[PlatformPublishJob]:
    """List jobs visible through the current user's account permissions."""
    statement = (
        select(PlatformPublishJob)
        .where(PlatformPublishJob.org_id == user.org_id)
        .order_by(PlatformPublishJob.id.desc())
        .limit(limit)
    )
    if account_id is not None:
        await require_account_access(session, user, account_id)
        statement = statement.where(PlatformPublishJob.account_id == account_id)
    rows = list(await session.scalars(statement))
    visible: list[PlatformPublishJob] = []
    for row in rows:
        try:
            await require_account_access(session, user, row.account_id)
        except HTTPException as exc:
            if exc.status_code in {403, 404}:
                continue
            raise
        visible.append(row)
    return visible


async def sync_publish_jobs_after_approval(
    session: AsyncSession,
    *,
    org_id: int,
    tool_call: AgentToolCall,
    approved: bool,
) -> None:
    """Keep durable publish jobs aligned with the approval source of truth."""
    jobs = list(
        await session.scalars(
            select(PlatformPublishJob).where(
                PlatformPublishJob.org_id == org_id,
                PlatformPublishJob.tool_call_id == tool_call.id,
                PlatformPublishJob.status
                == PlatformPublishJobStatus.PENDING_APPROVAL,
            )
        )
    )
    for job in jobs:
        previous_status = job.status
        job.approval_snapshot = _approval_snapshot(tool_call)
        job.status = (
            PlatformPublishJobStatus.TASK_CREATED
            if approved
            else PlatformPublishJobStatus.CANCELLED
        )
        _audit_transition(
            session,
            job,
            from_status=previous_status,
            to_status=job.status,
            reason=(
                "publish_approval_granted"
                if approved
                else "publish_approval_rejected"
            ),
        )


async def mark_publish_job_launched(
    session: AsyncSession,
    user: User,
    job_id: int,
) -> PlatformPublishJob:
    """Record that the user opened the official Douyin publishing handoff."""
    job = await _get_job_for_user(session, user, job_id, write=True)
    if job.status == PlatformPublishJobStatus.WAITING_BIND:
        return job
    if job.status != PlatformPublishJobStatus.HANDOFF_READY:
        raise PublishingServiceError(
            "PUBLISH_JOB_NOT_READY_TO_LAUNCH",
            "当前投稿任务还不能唤起抖音。",
        )
    if job.expires_at is not None and _as_utc(job.expires_at) <= datetime.now(UTC):
        previous_status = job.status
        job.status = PlatformPublishJobStatus.EXPIRED
        _audit_transition(
            session,
            job,
            from_status=previous_status,
            to_status=job.status,
            reason="douyin_handoff_expired_before_launch",
        )
        await session.commit()
        raise PublishingServiceError(
            "PUBLISH_HANDOFF_EXPIRED",
            "投稿链接已过期，请重新生成。",
        )

    previous_status = job.status
    job.status = PlatformPublishJobStatus.WAITING_BIND
    _audit_transition(
        session,
        job,
        from_status=previous_status,
        to_status=job.status,
        reason="douyin_handoff_launched",
    )
    await session.commit()
    await session.refresh(job)
    return job


async def retry_publish_job(
    session: AsyncSession,
    user: User,
    job_id: int,
) -> PlatformPublishJob:
    """Reset only ephemeral handoff state so an approved job can be retried."""
    job = await _get_job_for_user(session, user, job_id, write=True)
    if job.status not in {
        PlatformPublishJobStatus.FAILED,
        PlatformPublishJobStatus.EXPIRED,
    }:
        raise PublishingServiceError(
            "PUBLISH_JOB_NOT_RETRYABLE",
            "当前投稿任务不需要重试。",
        )
    await _approved_tool_call(session, job)

    previous_status = job.status
    job.status = PlatformPublishJobStatus.TASK_CREATED
    job.share_id = None
    job.expires_at = None
    job.handoff_started_at = None
    job.next_retry_at = None
    job.last_error_code = None
    job.last_error_message = None
    job.last_platform_log_id = None
    _audit_transition(
        session,
        job,
        from_status=previous_status,
        to_status=job.status,
        reason="publish_job_retry_requested",
    )
    await session.commit()
    await session.refresh(job)
    return job


async def cancel_publish_job(
    session: AsyncSession,
    user: User,
    job_id: int,
) -> PlatformPublishJob:
    """Cancel an unfinished job without deleting its audit history."""
    job = await _get_job_for_user(session, user, job_id, write=True)
    if job.status == PlatformPublishJobStatus.CANCELLED:
        return job
    if job.status in {
        PlatformPublishJobStatus.BOUND,
        PlatformPublishJobStatus.OBSERVING,
        PlatformPublishJobStatus.COMPLETED,
    }:
        raise PublishingServiceError(
            "PUBLISH_JOB_CANNOT_CANCEL",
            "作品已经绑定或进入数据观察阶段，不能取消投稿任务。",
        )

    previous_status = job.status
    job.status = PlatformPublishJobStatus.CANCELLED
    _audit_transition(
        session,
        job,
        from_status=previous_status,
        to_status=job.status,
        reason="publish_job_cancelled",
    )
    await session.commit()
    await session.refresh(job)
    return job


async def prepare_douyin_handoff(
    session: AsyncSession,
    user: User,
    job_id: int,
) -> PublishHandoffOut:
    """Prepare an ephemeral official H5 schema after durable approval checks."""
    job = await _get_job_for_user(session, user, job_id, write=True)
    tool_call = await _approved_tool_call(session, job)
    if not _tool_call_is_approved(tool_call):
        raise PublishingServiceError(
            "PUBLISH_APPROVAL_REQUIRED",
            "发布包需要先通过人工审批。",
        )
    if not settings.douyin_h5_publish_enabled:
        raise PublishingServiceError(
            "DOUYIN_H5_PUBLISH_DISABLED",
            "抖音官方投稿尚未在当前环境启用。",
            status_code=503,
        )
    if job.status in {
        PlatformPublishJobStatus.CANCELLED,
        PlatformPublishJobStatus.COMPLETED,
        PlatformPublishJobStatus.BOUND,
    }:
        raise PublishingServiceError(
            "PUBLISH_JOB_STATE_INVALID",
            "当前投稿任务状态不允许重新发起投稿。",
        )
    if job.expires_at is not None and _as_utc(job.expires_at) <= datetime.now(UTC):
        job.status = PlatformPublishJobStatus.EXPIRED
        await session.commit()

    account = await session.get(Account, job.account_id)
    if account is None or account.org_id != user.org_id:
        raise PublishingServiceError("PUBLISH_ACCOUNT_NOT_FOUND", "账号不存在。", status_code=404)
    integration, account_auth = await _load_ready_douyin_configuration(
        session, job=job, account=account
    )
    package = dict(job.publish_package or {})
    material, media_url = await _resolve_publish_media(
        session,
        org_id=job.org_id,
        package=package,
    )
    client_key = integration.client_key or ""
    client_secret = resolve_secret_ref(
        integration.client_secret_ref,
        platform=Platform.DOUYIN,
    )

    try:
        client_token = await get_douyin_client_token(
            org_id=job.org_id,
            client_key=client_key,
            client_secret=client_secret,
        )
        ticket = await get_douyin_open_ticket(
            org_id=job.org_id,
            client_key=client_key,
            client_secret=client_secret,
        )
        share_result = await create_douyin_share_id(
            client_token=client_token,
            default_hashtag=_first_topic(package),
        )
        share_id = share_result["share_id"]
        schema_url = build_douyin_h5_publish_schema(
            client_key=client_key,
            ticket=ticket,
            share_id=share_id,
            video_path=media_url if material.kind == "video" else None,
            image_path=media_url if material.kind == "image" else None,
            title=str(package.get("title") or ""),
            topics=_string_list(package.get("topics")),
            visibility=str(package.get("visibility") or "public"),
            allow_download=True,
            direct_to_publish=settings.douyin_direct_publish_enabled,
        )
    except DouyinIntegrationError as exc:
        _record_platform_error(job, exc)
        _audit_transition(
            session,
            job,
            from_status=job.status,
            to_status=PlatformPublishJobStatus.FAILED,
            reason="douyin_handoff_failed",
        )
        job.status = PlatformPublishJobStatus.FAILED
        await session.commit()
        raise PublishingServiceError(
            "DOUYIN_HANDOFF_FAILED",
            "抖音投稿准备失败，请稍后重试。",
            retryable=exc.retryable,
            status_code=502,
            details={"platform_error_code": exc.error_code or ""},
        ) from exc

    previous_status = job.status
    now = datetime.now(UTC)
    job.status = PlatformPublishJobStatus.HANDOFF_READY
    job.share_id = share_id
    job.expires_at = now + timedelta(minutes=55)
    job.handoff_started_at = now
    job.last_platform_log_id = share_result.get("log_id") or None
    job.last_error_code = None
    job.last_error_message = None
    job.capabilities_snapshot = {
        "h5_share": True,
        "open_ticket": True,
        "direct_publish": bool(settings.douyin_direct_publish_enabled),
        "account_open_id_present": bool(account_auth.external_open_id),
        "integration_scopes": list(integration.scopes or []),
    }
    job.approval_snapshot = _approval_snapshot(tool_call)
    _audit_transition(
        session,
        job,
        from_status=previous_status,
        to_status=job.status,
        reason="douyin_handoff_ready",
    )
    await session.commit()
    await session.refresh(job)
    return PublishHandoffOut(
        job=PublishJobOut.model_validate(job),
        schema_url=schema_url,
        expires_at=_as_utc(job.expires_at),
    )


async def ingest_douyin_create_video_callback(
    session: AsyncSession,
    callback: DouyinCreateVideoCallback,
) -> PlatformPublishJob:
    """Bind a callback to one job and project confirmed official work identity."""
    job = await session.scalar(
        select(PlatformPublishJob).where(
            PlatformPublishJob.platform == Platform.DOUYIN,
            PlatformPublishJob.share_id == callback.content.share_id,
        )
    )
    if job is None:
        raise PublishingServiceError(
            "DOUYIN_CALLBACK_JOB_NOT_FOUND",
            "找不到对应的投稿任务。",
            status_code=404,
        )
    integration = await session.scalar(
        select(PlatformIntegration).where(
            PlatformIntegration.org_id == job.org_id,
            PlatformIntegration.platform == Platform.DOUYIN.value,
            PlatformIntegration.client_key == callback.client_key,
        )
    )
    if integration is None:
        raise PublishingServiceError(
            "DOUYIN_CALLBACK_CLIENT_MISMATCH",
            "回调应用与投稿任务不一致。",
            status_code=403,
        )
    account_auth = await session.scalar(
        select(PlatformAccountAuth).where(
            PlatformAccountAuth.org_id == job.org_id,
            PlatformAccountAuth.account_id == job.account_id,
            PlatformAccountAuth.platform == Platform.DOUYIN.value,
        )
    )
    if (
        account_auth is None
        or not account_auth.external_open_id
        or account_auth.external_open_id != callback.from_user_id
    ):
        raise PublishingServiceError(
            "DOUYIN_CALLBACK_ACCOUNT_MISMATCH",
            "回调作者与投稿账号不一致。",
            status_code=403,
        )
    external_content_id = callback.content.item_id or callback.content.video_id
    if external_content_id is None:
        raise PublishingServiceError(
            "DOUYIN_CALLBACK_CONTENT_ID_MISSING",
            "回调未包含可绑定的作品 ID。",
            status_code=422,
        )
    if job.platform_content_record_id is not None:
        if (
            job.external_item_id == callback.content.item_id
            and job.external_video_id == callback.content.video_id
        ):
            return job
        raise PublishingServiceError(
            "DOUYIN_CALLBACK_IDENTITY_CONFLICT",
            "投稿任务已经绑定到其他作品。",
        )

    record = await session.scalar(
        select(PlatformContentRecord).where(
            PlatformContentRecord.account_id == job.account_id,
            PlatformContentRecord.platform == Platform.DOUYIN,
            PlatformContentRecord.external_content_id == external_content_id,
        )
    )
    published_at = callback.event_time or datetime.now(UTC)
    package = dict(job.publish_package or {})
    official_source_metadata = {
        "share_id": callback.content.share_id,
        "item_id": callback.content.item_id,
        "video_id": callback.content.video_id,
        "callback_log_id": callback.log_id,
        "has_default_hashtag": callback.content.has_default_hashtag,
    }
    if record is None:
        record = PlatformContentRecord(
            org_id=job.org_id,
            account_id=job.account_id,
            platform=Platform.DOUYIN,
            source_kind=DataSourceKind.OFFICIAL_API,
            source_metadata=official_source_metadata,
            external_content_id=external_content_id,
            title=str(package.get("title") or "") or None,
            published_at=published_at,
            content_format=str(package.get("content_type") or "") or None,
            review_status="published",
            identity_confidence=ContentIdentityConfidence.CONFIRMED,
        )
        session.add(record)
        await session.flush()
    else:
        record.title = record.title or str(package.get("title") or "") or None
        record.published_at = record.published_at or published_at
        record.identity_confidence = ContentIdentityConfidence.CONFIRMED
        record.source_kind = DataSourceKind.OFFICIAL_API
        record.source_metadata = {
            **dict(record.source_metadata or {}),
            **official_source_metadata,
        }

    previous_status = job.status
    job.platform_content_record_id = record.id
    job.external_item_id = callback.content.item_id
    job.external_video_id = callback.content.video_id
    job.last_platform_log_id = callback.log_id or job.last_platform_log_id
    job.bound_at = datetime.now(UTC)
    job.status = PlatformPublishJobStatus.BOUND
    _audit_transition(
        session,
        job,
        from_status=previous_status,
        to_status=job.status,
        reason="douyin_create_video_callback",
    )
    await session.commit()
    await session.refresh(job)
    return job


async def _get_job_for_user(
    session: AsyncSession,
    user: User,
    job_id: int,
    *,
    write: bool,
) -> PlatformPublishJob:
    job = await session.scalar(
        select(PlatformPublishJob).where(
            PlatformPublishJob.id == job_id,
            PlatformPublishJob.org_id == user.org_id,
        )
    )
    if job is None:
        raise PublishingServiceError("PUBLISH_JOB_NOT_FOUND", "投稿任务不存在。", status_code=404)
    roles = {WorkspaceRole.LEAD, WorkspaceRole.OPERATOR} if write else None
    await require_account_access(session, user, job.account_id, roles=roles)
    return job


async def _approved_tool_call(
    session: AsyncSession,
    job: PlatformPublishJob,
) -> AgentToolCall:
    tool_call = (
        await session.get(AgentToolCall, job.tool_call_id)
        if job.tool_call_id is not None
        else None
    )
    if tool_call is None or tool_call.org_id != job.org_id:
        raise PublishingServiceError(
            "PUBLISH_APPROVAL_REQUIRED",
            "发布包缺少有效的人工审批记录。",
        )
    return tool_call


async def _load_ready_douyin_configuration(
    session: AsyncSession,
    *,
    job: PlatformPublishJob,
    account: Account,
) -> tuple[PlatformIntegration, PlatformAccountAuth]:
    integration = await session.scalar(
        select(PlatformIntegration).where(
            PlatformIntegration.org_id == job.org_id,
            PlatformIntegration.platform == Platform.DOUYIN.value,
        )
    )
    account_auth = await session.scalar(
        select(PlatformAccountAuth).where(
            PlatformAccountAuth.org_id == job.org_id,
            PlatformAccountAuth.account_id == account.id,
            PlatformAccountAuth.platform == Platform.DOUYIN.value,
        )
    )
    if (
        integration is None
        or not integration.client_key
        or not integration.client_secret_ref
    ):
        raise PublishingServiceError(
            "DOUYIN_APP_NOT_CONFIGURED",
            "抖音开放平台应用尚未配置完整。",
            status_code=422,
        )
    if account_auth is None or account_auth.auth_status != "authorized":
        raise PublishingServiceError(
            "DOUYIN_ACCOUNT_NOT_AUTHORIZED",
            "当前抖音账号尚未完成官方授权。",
            status_code=422,
        )
    scopes = set(integration.scopes or [])
    required_scopes = {"open.get.ticket", "h5.share", "aweme.share"}
    missing_scopes = sorted(required_scopes - scopes)
    if missing_scopes:
        raise PublishingServiceError(
            "DOUYIN_PUBLISH_SCOPE_MISSING",
            "抖音应用缺少 H5 投稿所需能力。",
            status_code=422,
            details={
                "required": sorted(required_scopes),
                "missing": missing_scopes,
            },
        )
    return integration, account_auth


async def _resolve_publish_media(
    session: AsyncSession,
    *,
    org_id: int,
    package: dict[str, Any],
) -> tuple[MaterialAsset, str]:
    material_ids = package.get("material_ids")
    if not isinstance(material_ids, list) or len(material_ids) != 1:
        raise PublishingServiceError(
            "PUBLISH_MEDIA_COUNT_INVALID",
            "抖音 H5 投稿当前要求发布包只包含一个视频或一张图片。",
            status_code=422,
        )
    material = await session.scalar(
        select(MaterialAsset).where(
            MaterialAsset.id == material_ids[0],
            MaterialAsset.org_id == org_id,
        )
    )
    if material is None or material.status != MaterialStatus.READY:
        raise PublishingServiceError(
            "PUBLISH_MEDIA_NOT_READY",
            "发布素材不存在或尚未就绪。",
            status_code=422,
        )
    if material.kind not in {"video", "image"}:
        raise PublishingServiceError(
            "PUBLISH_MEDIA_TYPE_UNSUPPORTED",
            "当前素材类型不能用于抖音 H5 投稿。",
            status_code=422,
        )
    media_url = (material.source_url or "").strip()
    parsed = urlparse(media_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise PublishingServiceError(
            "PUBLISH_MEDIA_NOT_PUBLIC",
            "素材尚未生成可供抖音访问的 HTTPS 地址。",
            status_code=422,
        )
    return material, media_url


async def _validate_frozen_workspace_context(
    session: AsyncSession,
    *,
    account: Account,
    active_client_id: int | None,
    active_project_id: int | None,
) -> None:
    if active_client_id is not None:
        client_bound = await session.scalar(
            select(Account.id).where(
                Account.id == account.id,
                Account.org_id == account.org_id,
                or_(
                    Account.client_id == active_client_id,
                    Account.id.in_(
                        select(AccountClient.account_id).where(
                            AccountClient.client_id == active_client_id
                        )
                    ),
                ),
            )
        )
        if client_bound is None:
            raise PublishingServiceError(
                "PUBLISH_CLIENT_CONTEXT_MISMATCH",
                "所选客户未绑定到当前账号。",
                status_code=422,
            )
    if active_project_id is not None:
        project = await session.scalar(
            select(Project).where(
                Project.id == active_project_id,
                Project.org_id == account.org_id,
            )
        )
        project_bound = await session.scalar(
            select(Account.id).where(
                Account.id == account.id,
                or_(
                    Account.project_id == active_project_id,
                    Account.id.in_(
                        select(ProjectAccount.account_id).where(
                            ProjectAccount.project_id == active_project_id
                        )
                    ),
                ),
            )
        )
        if project is None or project_bound is None:
            raise PublishingServiceError(
                "PUBLISH_PROJECT_CONTEXT_MISMATCH",
                "所选项目未绑定到当前账号。",
                status_code=422,
            )
        if active_client_id is not None and project.client_id != active_client_id:
            raise PublishingServiceError(
                "PUBLISH_WORKSPACE_CONTEXT_MISMATCH",
                "所选项目不属于当前客户。",
                status_code=422,
            )


def _assert_idempotent_request(
    existing: PlatformPublishJob,
    request: CreatePublishJobRequest,
) -> None:
    incoming = request.publish_package.model_dump(mode="json")
    if (
        existing.account_id != request.account_id
        or existing.active_client_id != request.active_client_id
        or existing.active_project_id != request.active_project_id
        or existing.tool_call_id != request.tool_call_id
        or _canonical(existing.publish_package) != _canonical(incoming)
    ):
        raise PublishingServiceError(
            "PUBLISH_IDEMPOTENCY_CONFLICT",
            "该幂等键已用于不同的投稿请求。",
        )


def _assert_approved_package_matches(
    tool_call: AgentToolCall,
    incoming: dict[str, Any],
) -> None:
    approved = (tool_call.meta or {}).get("publish_package")
    if not isinstance(approved, dict):
        raise PublishingServiceError(
            "PUBLISH_APPROVAL_PACKAGE_MISSING",
            "审批记录中缺少发布包快照。",
            status_code=422,
        )
    ignored = {"execution_mode", "manual_steps"}
    approved_comparable = {key: value for key, value in approved.items() if key not in ignored}
    incoming_comparable = {key: value for key, value in incoming.items() if key not in ignored}
    if _canonical(approved_comparable) != _canonical(incoming_comparable):
        raise PublishingServiceError(
            "PUBLISH_APPROVAL_PACKAGE_MISMATCH",
            "当前发布包与已审批版本不一致，请重新审批。",
            status_code=422,
        )


def _tool_call_is_approved(tool_call: AgentToolCall) -> bool:
    meta = tool_call.meta or {}
    decision = meta.get("decision")
    return bool(
        tool_call.tool_code == "publish_package_prepare"
        and tool_call.requires_human_confirmation
        and tool_call.status == "success"
        and isinstance(decision, dict)
        and decision.get("approved") is True
        and meta.get("publish_decision_status") == "approved_for_manual_publish"
    )


def _approval_snapshot(tool_call: AgentToolCall) -> dict[str, Any]:
    meta = tool_call.meta or {}
    decision = meta.get("decision")
    safe_decision = decision if isinstance(decision, dict) else {}
    return {
        "tool_call_id": tool_call.id,
        "tool_code": tool_call.tool_code,
        "status": tool_call.status,
        "approved": bool(safe_decision.get("approved")),
        "reviewed_by": safe_decision.get("reviewed_by"),
        "reviewed_at": safe_decision.get("reviewed_at"),
    }


def _audit_transition(
    session: AsyncSession,
    job: PlatformPublishJob,
    *,
    from_status: PlatformPublishJobStatus | None,
    to_status: PlatformPublishJobStatus,
    reason: str,
) -> None:
    session.add(
        Event(
            type="platform.publish_job.transition",
            project_id=job.active_project_id,
            payload={
                "org_id": job.org_id,
                "job_id": job.id,
                "account_id": job.account_id,
                "from_status": from_status.value if from_status else None,
                "to_status": to_status.value,
                "reason": reason,
                "retry_count": job.retry_count,
                "platform_log_id": job.last_platform_log_id,
                "error_code": job.last_error_code,
            },
        )
    )


def _record_platform_error(
    job: PlatformPublishJob,
    error: DouyinIntegrationError,
) -> None:
    job.last_error_code = error.error_code or "DOUYIN_INTEGRATION_ERROR"
    job.last_error_message = "抖音开放平台请求失败"
    job.last_platform_log_id = error.log_id
    job.retry_count += 1


def _first_topic(package: dict[str, Any]) -> str | None:
    topics = _string_list(package.get("topics"))
    return topics[0] if topics else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
