"""Production-safe official publishing orchestration.

This service owns tenant, approval, idempotency, and state-transition checks.
Platform integrations only perform transport work and never decide whether an
external action is allowed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import CursorResult, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.core.workspace_access import accessible_account_clause, require_account_access
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
    ArticleImageSlot,
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
    WechatDraftMapping,
)
from app.models.enums import (
    AccountStatus,
    ArticleImageSlotStatus,
    ContentIdentityConfidence,
    DataSourceKind,
    DeliverableStatus,
    DeliverableType,
    MaterialStatus,
    Platform,
    WorkspaceRole,
)
from app.models.publishing import (
    PlatformPublishJob,
    PlatformPublishJobOperationType,
    PlatformPublishJobStatus,
)
from app.schemas.orchestrator import PublishPackageOut
from app.schemas.platform import WechatCapabilitySnapshot
from app.schemas.publishing import (
    CreatePublishJobRequest,
    DouyinCreateVideoCallback,
    PublishHandoffOut,
    PublishJobOut,
    SyncWechatDraftRequest,
)
from app.schemas.wechat_article import ArticleDocument, WechatDraftArticle
from app.schemas.wechat_article import ArticleDraftSyncContextOut, ArticleSyncReadinessOut
from app.services.deliverable_streams import deliverable_stream_clause
from app.services.turn_events import TurnEventScope, append_turn_event
from app.services.wechat_articles import ARTICLE_VERSION_AGENT_CODE, validate_article_for_sync
from app.services.wechat_component import WechatIntegrationError
from app.services.wechat_drafts import WechatDraftIntegrationError, compute_remote_hash
from app.services.wechat_renderer import render_wechat_article


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

_MAX_SYNC_IMAGE_BYTES = 20 * 1024 * 1024
_SYNC_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


async def prepare_wechat_draft_sync_job(
    session: AsyncSession,
    user: User,
    *,
    article_id: int,
    request: SyncWechatDraftRequest,
) -> PlatformPublishJob:
    """Freeze one exact account-scoped package before any external interaction."""
    source = await session.execute(
        select(Deliverable, ContentItem, Account)
        .join(ContentItem, Deliverable.content_item_id == ContentItem.id)
        .join(Account, ContentItem.account_id == Account.id)
        .where(
            Deliverable.id == request.article_version_id,
            Deliverable.content_item_id == article_id,
            Deliverable.agent_code == ARTICLE_VERSION_AGENT_CODE,
            Deliverable.type == DeliverableType.WECHAT_ARTICLE,
            ContentItem.account_id == Account.id,
            Account.org_id == user.org_id,
        )
    )
    lineage = source.one_or_none()
    if lineage is None:
        raise PublishingServiceError(
            "WECHAT_ARTICLE_VERSION_NOT_FOUND",
            "找不到当前账号对应的公众号文章版本。",
            status_code=404,
        )
    deliverable, _content_item, account = lineage
    await require_account_access(
        session,
        user,
        account.id,
        roles={WorkspaceRole.LEAD, WorkspaceRole.OPERATOR},
    )
    if account.platform is not Platform.WECHAT_OFFICIAL_ACCOUNT:
        raise PublishingServiceError(
            "WECHAT_ACCOUNT_REQUIRED",
            "当前文章不属于微信公众号账号。",
            status_code=422,
        )
    if account.status is not AccountStatus.ACTIVE:
        raise PublishingServiceError(
            "WECHAT_ACCOUNT_INACTIVE",
            "当前微信公众号账号不可用于草稿同步。",
            status_code=422,
        )
    await _append_deliverable_sync_step(
        session,
        deliverable,
        org_id=user.org_id,
        account_id=account.id,
        idempotency_key=request.idempotency_key,
        step="readiness",
        phase="started",
    )
    readiness = await validate_article_for_sync(session, version_id=deliverable.id)
    if not readiness.can_sync:
        await _append_deliverable_sync_step(
            session,
            deliverable,
            org_id=user.org_id,
            account_id=account.id,
            idempotency_key=request.idempotency_key,
            step="readiness",
            phase="failed",
            error_code="WECHAT_ARTICLE_NOT_READY",
        )
        raise PublishingServiceError(
            "WECHAT_ARTICLE_NOT_READY",
            "文章尚未满足同步条件。",
            status_code=422,
            details={"blockerCodes": [item.code for item in readiness.blockers]},
        )
    await _append_deliverable_sync_step(
        session,
        deliverable,
        org_id=user.org_id,
        account_id=account.id,
        idempotency_key=request.idempotency_key,
        step="readiness",
        phase="completed",
    )

    document = ArticleDocument.model_validate(deliverable.payload["document"])
    body_slot_keys = sorted(
        {
            slot_key
            for block in document.blocks
            if isinstance((slot_key := getattr(block, "slot_key", None)), str)
        }
    )
    required_keys = ["cover", *body_slot_keys]
    slots = list(
        await session.scalars(
            select(ArticleImageSlot).where(
                ArticleImageSlot.content_item_id == article_id,
                ArticleImageSlot.account_id == account.id,
                ArticleImageSlot.stable_key.in_(required_keys),
            )
        )
    )
    slots_by_key = {slot.stable_key: slot for slot in slots}
    if set(slots_by_key) != set(required_keys):
        raise PublishingServiceError(
            "WECHAT_ARTICLE_ASSETS_NOT_READY",
            "封面或正文配图尚未选择。",
            status_code=422,
        )

    frozen_assets: dict[str, dict[str, Any]] = {}
    for stable_key in required_keys:
        slot = slots_by_key[stable_key]
        if slot.status is not ArticleImageSlotStatus.SELECTED or slot.selected_material_id is None:
            raise PublishingServiceError(
                "WECHAT_ARTICLE_ASSETS_NOT_READY",
                "封面或正文配图尚未选择。",
                status_code=422,
            )
        material = await session.scalar(
            select(MaterialAsset).where(
                MaterialAsset.id == slot.selected_material_id,
                MaterialAsset.org_id == user.org_id,
                MaterialAsset.content_item_id == article_id,
                MaterialAsset.kind == "image",
                MaterialAsset.status == MaterialStatus.READY,
            )
        )
        if material is None or not material.local_path:
            raise PublishingServiceError(
                "WECHAT_ARTICLE_ASSETS_NOT_READY",
                "封面或正文配图不可用。",
                status_code=422,
            )
        frozen_assets[stable_key] = _frozen_image_fact(material)

    mapping = await session.scalar(
        select(WechatDraftMapping).where(
            WechatDraftMapping.org_id == user.org_id,
            WechatDraftMapping.account_id == account.id,
            WechatDraftMapping.content_item_id == article_id,
        )
    )
    _validate_remote_strategy(mapping, request)
    package = {
        "article_id": article_id,
        "article_version_id": deliverable.id,
        "conflict_strategy": request.conflict_strategy,
        "document_hash": hashlib.sha256(
            _canonical(document.model_dump(mode="json")).encode("utf-8")
        ).hexdigest(),
        "body_assets": [{"stable_key": key, **frozen_assets[key]} for key in body_slot_keys],
        "cover_asset": frozen_assets["cover"],
        "initial_mapping": (
            {
                "media_id": mapping.media_id,
                "remote_hash": mapping.remote_hash,
                "last_synced_deliverable_id": mapping.last_synced_deliverable_id,
            }
            if mapping is not None
            else None
        ),
        "progress": {"body_urls": {}, "cover_media_id": None, "draft_completed": False},
    }
    digest = hashlib.sha256(
        _canonical(
            {
                "org_id": user.org_id,
                "account_id": account.id,
                "article_id": article_id,
                "article_version_id": deliverable.id,
                "conflict_strategy": request.conflict_strategy,
                "expected_remote_hash": request.expected_remote_hash,
                "package": {key: value for key, value in package.items() if key != "progress"},
            }
        ).encode("utf-8")
    ).hexdigest()

    existing = await session.scalar(
        select(PlatformPublishJob).where(
            PlatformPublishJob.org_id == user.org_id,
            PlatformPublishJob.idempotency_key == request.idempotency_key,
        )
    )
    if existing is not None:
        _assert_wechat_sync_digest(existing, digest)
        return existing

    job = PlatformPublishJob(
        org_id=user.org_id,
        account_id=account.id,
        created_by_id=user.id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        operation_type=PlatformPublishJobOperationType.WECHAT_DRAFT_SYNC,
        status=PlatformPublishJobStatus.WECHAT_QUEUED,
        idempotency_key=request.idempotency_key,
        article_version_id=deliverable.id,
        expected_remote_hash=request.expected_remote_hash,
        request_digest=digest,
        publish_package=package,
        capabilities_snapshot={},
        approval_snapshot={
            "actor_id": user.id,
            "account_id": account.id,
            "article_id": article_id,
            "article_version_id": deliverable.id,
            "conflict_strategy": request.conflict_strategy,
            "expected_remote_hash": request.expected_remote_hash,
            "request_digest": digest,
            "approved_at": datetime.now(UTC).isoformat(),
        },
    )
    try:
        async with session.begin_nested():
            session.add(job)
            await session.flush()
    except IntegrityError:
        winner = await session.scalar(
            select(PlatformPublishJob).where(
                PlatformPublishJob.org_id == user.org_id,
                PlatformPublishJob.idempotency_key == request.idempotency_key,
            )
        )
        if winner is None:
            raise
        _assert_wechat_sync_digest(winner, digest)
        return winner
    await session.commit()
    await session.refresh(job)
    return job


async def get_wechat_draft_sync_context(
    session: AsyncSession,
    user: User,
    *,
    article_id: int,
    article_version_id: int,
) -> ArticleDraftSyncContextOut:
    """Return a read-only preflight projection without creating any sync side effects."""
    source = await session.execute(
        select(Deliverable, ContentItem, Account)
        .join(ContentItem, Deliverable.content_item_id == ContentItem.id)
        .join(Account, ContentItem.account_id == Account.id)
        .where(
            Deliverable.id == article_version_id,
            Deliverable.content_item_id == article_id,
            Deliverable.agent_code == ARTICLE_VERSION_AGENT_CODE,
            Deliverable.type == DeliverableType.WECHAT_ARTICLE,
            ContentItem.account_id == Account.id,
            Account.org_id == user.org_id,
            await accessible_account_clause(session, user),
        )
    )
    lineage = source.one_or_none()
    if lineage is None:
        raise PublishingServiceError(
            "WECHAT_ARTICLE_VERSION_NOT_FOUND",
            "????????????????????",
            status_code=404,
        )
    deliverable, _content_item, account = lineage
    document = ArticleDocument.model_validate(deliverable.payload["document"])
    readiness = await validate_article_for_sync(session, version_id=deliverable.id)
    image_count = await session.scalar(
        select(func.count(ArticleImageSlot.id)).where(
            ArticleImageSlot.content_item_id == article_id,
            ArticleImageSlot.account_id == account.id,
            ArticleImageSlot.selected_material_id.is_not(None),
        )
    )
    mapping = await session.scalar(
        select(WechatDraftMapping).where(
            WechatDraftMapping.org_id == user.org_id,
            WechatDraftMapping.account_id == account.id,
            WechatDraftMapping.content_item_id == article_id,
        )
    )
    latest_job = await session.scalar(
        select(PlatformPublishJob)
        .join(Deliverable, PlatformPublishJob.article_version_id == Deliverable.id)
        .where(
            PlatformPublishJob.org_id == user.org_id,
            PlatformPublishJob.account_id == account.id,
            PlatformPublishJob.operation_type == PlatformPublishJobOperationType.WECHAT_DRAFT_SYNC,
            Deliverable.content_item_id == article_id,
        )
        .order_by(PlatformPublishJob.updated_at.desc(), PlatformPublishJob.id.desc())
        .limit(1)
    )
    remote = None
    if latest_job is not None:
        remote_hash = latest_job.observed_remote_hash
        if remote_hash is None and mapping is not None:
            remote_hash = mapping.remote_hash
        remote = {
            "status": latest_job.status.value,
            "remoteHash": remote_hash,
            "updatedAt": latest_job.updated_at,
            "errorCode": latest_job.last_error_code,
            "operationType": latest_job.operation_type.value,
        }
    return ArticleDraftSyncContextOut.model_validate(
        {
            "targetAccount": {"id": account.id, "name": account.nickname},
            "articleTitle": document.title,
            "articleVersionId": deliverable.id,
            "imageCount": image_count or 0,
            "readiness": _article_sync_readiness_out(readiness),
            "remote": remote,
        }
    )


def _frozen_image_fact(material: MaterialAsset) -> dict[str, Any]:
    root = Path(settings.storage_local_dir).resolve()
    candidate = (root / (material.local_path or "")).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise PublishingServiceError(
            "WECHAT_ARTICLE_ASSET_UNAVAILABLE",
            "文章图片文件不可用。",
            status_code=422,
        )
    size = candidate.stat().st_size
    if size <= 0 or size > _MAX_SYNC_IMAGE_BYTES:
        raise PublishingServiceError(
            "WECHAT_ARTICLE_ASSET_UNAVAILABLE",
            "文章图片文件大小不符合要求。",
            status_code=422,
        )
    media_type = _SYNC_IMAGE_MEDIA_TYPES.get(candidate.suffix.lower())
    if media_type is None:
        raise PublishingServiceError(
            "WECHAT_ARTICLE_ASSET_UNAVAILABLE",
            "文章图片格式不受支持。",
            status_code=422,
        )
    content = candidate.read_bytes()
    if len(content) != size:
        raise PublishingServiceError(
            "WECHAT_ARTICLE_ASSET_UNAVAILABLE",
            "文章图片读取失败。",
            status_code=422,
        )
    return {
        "material_id": material.id,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": size,
        "media_type": media_type,
    }


def _validate_remote_strategy(
    mapping: WechatDraftMapping | None,
    request: SyncWechatDraftRequest,
) -> None:
    if mapping is None:
        if request.conflict_strategy == "overwrite_confirmed":
            raise PublishingServiceError(
                "WECHAT_DRAFT_OVERWRITE_REQUIRES_MAPPING",
                "当前文章尚无可覆盖的微信草稿。",
                status_code=422,
            )
        if request.expected_remote_hash is not None:
            raise PublishingServiceError(
                "WECHAT_DRAFT_UNEXPECTED_REMOTE_HASH",
                "新建微信草稿时不应提交远端版本确认值。",
                status_code=422,
            )
        return
    if (
        request.conflict_strategy in {"fail", "overwrite_confirmed"}
        and not (request.expected_remote_hash or "").strip()
    ):
        raise PublishingServiceError(
            "WECHAT_DRAFT_EXPECTED_HASH_REQUIRED",
            "更新微信草稿前必须确认当前远端版本。",
            status_code=422,
        )


def _assert_wechat_sync_digest(job: PlatformPublishJob, digest: str) -> None:
    if (
        job.operation_type is not PlatformPublishJobOperationType.WECHAT_DRAFT_SYNC
        or job.request_digest != digest
    ):
        raise PublishingServiceError(
            "WECHAT_DRAFT_IDEMPOTENCY_CONFLICT",
            "该幂等键已用于不同的微信草稿同步请求。",
            status_code=409,
        )


async def execute_wechat_draft_sync_job(
    session: AsyncSession,
    user: User,
    *,
    job_id: int,
    capability_probe: Any,
    token_provider: Any,
    draft_client: Any,
) -> PlatformPublishJob:
    """Execute a frozen new-draft package without holding DB work over provider I/O."""
    job = await _get_wechat_sync_job(session, user, job_id)
    if job.status is PlatformPublishJobStatus.WECHAT_SYNCED:
        return job
    _validate_wechat_sync_approval(job)
    await _append_wechat_sync_step(session, job, "capabilities", "started")
    await _guard_unresolved_sync_intent(session, job)
    if job.status not in {
        PlatformPublishJobStatus.WECHAT_QUEUED,
        PlatformPublishJobStatus.WECHAT_RUNNING,
    }:
        raise PublishingServiceError(
            "WECHAT_DRAFT_SYNC_NOT_RUNNABLE",
            "当前微信草稿同步任务不可执行。",
            status_code=409,
        )
    if job.status is PlatformPublishJobStatus.WECHAT_QUEUED:
        expected_attempt = job.retry_count
        claimed = await session.execute(
            update(PlatformPublishJob)
            .where(
                PlatformPublishJob.id == job.id,
                PlatformPublishJob.status == PlatformPublishJobStatus.WECHAT_QUEUED,
                PlatformPublishJob.retry_count == expected_attempt,
            )
            .values(
                status=PlatformPublishJobStatus.WECHAT_RUNNING,
                retry_count=expected_attempt + 1,
            )
        )
        if not isinstance(claimed, CursorResult) or claimed.rowcount != 1:
            await session.rollback()
            raise PublishingServiceError(
                "WECHAT_DRAFT_SYNC_ALREADY_RUNNING",
                "微信草稿同步任务已由其他执行器接管。",
                status_code=409,
            )
        await session.commit()
        job = await _get_wechat_sync_job(session, user, job.id)

    try:
        capabilities = WechatCapabilitySnapshot.model_validate(
            await capability_probe(session, job.account_id)
        )
    except (WechatIntegrationError, ValueError) as exc:
        await _commit_boundary_failure_without_intent(
            session,
            job,
            error=exc,
            fallback_code="WECHAT_DRAFT_CAPABILITY_PROBE_FAILED",
        )
        await _append_wechat_sync_step(
            session,
            job,
            "capabilities",
            "failed",
            error_code="WECHAT_DRAFT_CAPABILITY_PROBE_FAILED",
        )
        raise _safe_external_service_error(exc) from None
    package = dict(job.publish_package or {})
    initial_mapping = package.get("initial_mapping")
    strategy = str(package["conflict_strategy"])
    required_capabilities = [capabilities.add_permanent_material]
    required_capabilities.append(
        capabilities.draft_update
        if initial_mapping is not None and strategy != "create_new"
        else capabilities.draft_add
    )
    if initial_mapping is not None and strategy != "create_new":
        required_capabilities.append(capabilities.draft_get)
    if package.get("body_assets"):
        required_capabilities.append(capabilities.upload_article_image)
    if not all(item.can_use for item in required_capabilities):
        job.capabilities_snapshot = _safe_capability_snapshot(capabilities)
        job.status = PlatformPublishJobStatus.WECHAT_BLOCKED
        job.last_error_code = "WECHAT_DRAFT_CAPABILITY_MISSING"
        job.last_error_message = "微信公众号缺少草稿同步所需能力"
        await session.commit()
        await _append_wechat_sync_step(
            session,
            job,
            "capabilities",
            "failed",
            error_code="WECHAT_DRAFT_CAPABILITY_MISSING",
        )
        return job
    job.capabilities_snapshot = _safe_capability_snapshot(capabilities)
    await session.commit()

    try:
        access_token = await token_provider.get_authorizer_access_token(session, job.account_id)
    except WechatIntegrationError as exc:
        await _commit_boundary_failure_without_intent(
            session,
            job,
            error=exc,
            fallback_code="WECHAT_DRAFT_AUTHORIZATION_REVOKED",
        )
        await _append_wechat_sync_step(
            session,
            job,
            "capabilities",
            "failed",
            error_code="WECHAT_DRAFT_AUTHORIZATION_REVOKED",
        )
        retryable = _is_retryable_external_error(exc)
        raise PublishingServiceError(
            "WECHAT_DRAFT_EXTERNAL_RETRYABLE"
            if retryable
            else "WECHAT_DRAFT_AUTHORIZATION_REVOKED",
            "微信公众号授权服务暂时不可用，请稍后重试。"
            if retryable
            else "微信公众号授权已失效，请重新授权后再同步。",
            retryable=retryable,
            status_code=503 if retryable else 422,
        ) from None
    await session.commit()
    await _append_wechat_sync_step(session, job, "capabilities", "completed")
    await _append_wechat_sync_step(session, job, "assets", "started")
    await _append_wechat_sync_step(session, job, "assets", "completed")
    await _append_wechat_sync_step(session, job, "conflict", "started")
    progress = dict(package.get("progress") or {})

    update_media_id: str | None = None
    if initial_mapping is not None and strategy != "create_new":
        update_media_id = str(initial_mapping["media_id"])
        await _commit_external_intent(session, job, operation="draft_get")
        try:
            remote = await draft_client.get_draft(
                access_token=access_token,
                media_id=update_media_id,
            )
        except WechatDraftIntegrationError as exc:
            await _commit_external_failure(session, job, operation="draft_get", error=exc)
            await _append_wechat_sync_step(
                session,
                job,
                "conflict",
                "failed",
                error_code="WECHAT_DRAFT_EXTERNAL_FAILURE",
            )
            raise PublishingServiceError(
                "WECHAT_DRAFT_EXTERNAL_RETRYABLE"
                if exc.retryable
                else "WECHAT_DRAFT_EXTERNAL_FAILURE",
                "微信公众号暂时无法读取草稿。" if exc.retryable else "微信公众号返回了无效草稿。",
                retryable=exc.retryable,
                status_code=503 if exc.retryable else 422,
            ) from None
        observed_hash = compute_remote_hash(remote.news_item[0])
        job.observed_remote_hash = observed_hash
        await _commit_external_result(session, job, operation="draft_get")
        expected = job.expected_remote_hash
        stored_hash = initial_mapping.get("remote_hash")
        accepted = (
            observed_hash == expected == stored_hash
            if strategy == "fail"
            else observed_hash == expected
        )
        if not accepted:
            job.status = PlatformPublishJobStatus.WECHAT_CONFLICT
            job.last_error_code = "WECHAT_DRAFT_CONFLICT"
            job.last_error_message = "微信草稿已发生变化"
            await session.commit()
            await _append_wechat_sync_step(
                session,
                job,
                "conflict",
                "failed",
                error_code="WECHAT_DRAFT_CONFLICT",
            )
            raise PublishingServiceError(
                "WECHAT_DRAFT_CONFLICT",
                "微信草稿已被修改，请确认最新版本后再同步。",
                status_code=409,
                details={
                    "syncId": job.id,
                    "observedRemoteHash": observed_hash,
                },
            )
    await _append_wechat_sync_step(session, job, "conflict", "completed")
    await _append_wechat_sync_step(session, job, "sync", "started")
    body_urls = dict(progress.get("body_urls") or {})
    for body_fact_value in package.get("body_assets") or []:
        body_fact = dict(body_fact_value)
        stable_key = str(body_fact.pop("stable_key"))
        if stable_key in body_urls:
            continue
        body_bytes, body_media_type = await _load_frozen_asset(
            session,
            job,
            body_fact,
        )
        operation = f"body_upload:{stable_key}"
        await _commit_external_intent(session, job, operation=operation)
        try:
            body_url = await draft_client.upload_article_image(
                access_token=access_token,
                filename=(f"body-{body_fact['material_id']}{_media_suffix(body_media_type)}"),
                content=body_bytes,
                media_type=body_media_type,
            )
        except WechatDraftIntegrationError as exc:
            await _commit_external_failure(session, job, operation=operation, error=exc)
            await _append_wechat_sync_step(
                session,
                job,
                "sync",
                "failed",
                error_code="WECHAT_DRAFT_EXTERNAL_FAILURE",
            )
            raise PublishingServiceError(
                "WECHAT_DRAFT_EXTERNAL_RETRYABLE"
                if exc.retryable
                else "WECHAT_DRAFT_EXTERNAL_FAILURE",
                "微信公众号暂时无法处理图片。"
                if exc.retryable
                else "微信公众号拒绝了图片处理请求。",
                retryable=exc.retryable,
                status_code=503 if exc.retryable else 422,
            ) from None
        body_urls[stable_key] = body_url
        progress["body_urls"] = body_urls
        package["progress"] = progress
        job.publish_package = package
        flag_modified(job, "publish_package")
        await _commit_external_result(session, job, operation=operation)
    cover_fact = dict(package["cover_asset"])
    if progress.get("cover_media_id") is None:
        cover_bytes, cover_media_type = await _load_frozen_asset(
            session,
            job,
            cover_fact,
        )
        await _commit_external_intent(session, job, operation="cover_upload")
        try:
            cover_media_id = await draft_client.add_permanent_cover(
                access_token=access_token,
                filename=(f"cover-{cover_fact['material_id']}{_media_suffix(cover_media_type)}"),
                content=cover_bytes,
                media_type=cover_media_type,
            )
        except WechatDraftIntegrationError as exc:
            await _commit_external_failure(session, job, operation="cover_upload", error=exc)
            await _append_wechat_sync_step(
                session,
                job,
                "sync",
                "failed",
                error_code="WECHAT_DRAFT_EXTERNAL_FAILURE",
            )
            raise _safe_external_service_error(exc) from None
        progress["cover_media_id"] = cover_media_id
        package["progress"] = progress
        job.publish_package = package
        flag_modified(job, "publish_package")
        await _commit_external_result(session, job, operation="cover_upload")

    version = await session.scalar(
        select(Deliverable).where(
            Deliverable.id == job.article_version_id,
            Deliverable.content_item_id == package["article_id"],
            Deliverable.type == DeliverableType.WECHAT_ARTICLE,
            Deliverable.agent_code == ARTICLE_VERSION_AGENT_CODE,
        )
    )
    if version is None:
        raise PublishingServiceError(
            "WECHAT_ARTICLE_VERSION_NOT_FOUND",
            "找不到已批准的公众号文章版本。",
            status_code=404,
        )
    document = ArticleDocument.model_validate(version.payload["document"])
    document_hash = hashlib.sha256(
        _canonical(document.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()
    if document_hash != package["document_hash"]:
        raise PublishingServiceError(
            "WECHAT_ARTICLE_VERSION_CHANGED",
            "已批准的公众号文章版本发生异常变化。",
            status_code=409,
        )
    rendered = render_wechat_article(document, asset_map=body_urls)
    outbound = WechatDraftArticle(
        title=document.title,
        author=document.author,
        digest=document.digest,
        content=rendered.normalized_html,
        thumb_media_id=progress["cover_media_id"],
        need_open_comment=1,
        only_fans_can_comment=0,
        content_source_url=None,
    )
    operation = "draft_update" if update_media_id is not None else "draft_add"
    await _commit_external_intent(session, job, operation=operation)
    try:
        if update_media_id is None:
            media_id = await draft_client.add_draft(
                access_token=access_token,
                article=outbound,
            )
        else:
            await draft_client.update_draft(
                access_token=access_token,
                media_id=update_media_id,
                index=0,
                article=outbound,
            )
            media_id = update_media_id
    except WechatDraftIntegrationError as exc:
        await _commit_external_failure(session, job, operation=operation, error=exc)
        await _append_wechat_sync_step(
            session,
            job,
            "sync",
            "failed",
            error_code="WECHAT_DRAFT_EXTERNAL_FAILURE",
        )
        raise _safe_external_service_error(exc) from None
    remote_hash = compute_remote_hash(outbound)
    mapping = await session.scalar(
        select(WechatDraftMapping).where(
            WechatDraftMapping.org_id == job.org_id,
            WechatDraftMapping.account_id == job.account_id,
            WechatDraftMapping.content_item_id == int(package["article_id"]),
        )
    )
    if mapping is None:
        mapping = WechatDraftMapping(
            org_id=job.org_id,
            account_id=job.account_id,
            content_item_id=int(package["article_id"]),
            media_id=media_id,
        )
        session.add(mapping)
    mapping.media_id = media_id
    mapping.remote_hash = remote_hash
    mapping.last_synced_deliverable_id = job.article_version_id
    job.external_media_id = media_id
    job.observed_remote_hash = remote_hash
    job.status = PlatformPublishJobStatus.WECHAT_SYNCED
    progress["draft_completed"] = True
    package["progress"] = progress
    job.publish_package = package
    flag_modified(job, "publish_package")
    await _commit_external_result(session, job, operation=operation)
    await _append_wechat_sync_step(session, job, "sync", "completed")
    await session.commit()
    await session.refresh(job)
    return job


async def _get_wechat_sync_job(
    session: AsyncSession,
    user: User,
    job_id: int,
) -> PlatformPublishJob:
    job = await session.scalar(
        select(PlatformPublishJob).where(
            PlatformPublishJob.id == job_id,
            PlatformPublishJob.org_id == user.org_id,
            PlatformPublishJob.operation_type == PlatformPublishJobOperationType.WECHAT_DRAFT_SYNC,
        )
    )
    if job is None:
        raise PublishingServiceError(
            "WECHAT_DRAFT_SYNC_NOT_FOUND",
            "找不到微信草稿同步任务。",
            status_code=404,
        )
    await require_account_access(session, user, job.account_id)
    return job


async def get_wechat_draft_sync_job(
    session: AsyncSession,
    user: User,
    job_id: int,
) -> PlatformPublishJob:
    """Public account-scoped read boundary for one draft-sync ledger row."""
    return await _get_wechat_sync_job(session, user, job_id)


def wechat_draft_sync_out(job: PlatformPublishJob) -> dict[str, Any]:
    package = job.publish_package if isinstance(job.publish_package, dict) else {}
    approval = job.approval_snapshot if isinstance(job.approval_snapshot, dict) else {}
    strategy = approval.get("conflict_strategy") or package.get("conflict_strategy")
    return {
        "id": job.id,
        "account_id": job.account_id,
        "article_id": int(package["article_id"]),
        "article_version_id": job.article_version_id,
        "status": job.status,
        "conflict_strategy": strategy,
        "external_media_id": job.external_media_id,
        "expected_remote_hash": job.expected_remote_hash,
        "observed_remote_hash": job.observed_remote_hash,
        "retryable": job.status is PlatformPublishJobStatus.WECHAT_QUEUED and job.retry_count > 0,
        "error_code": job.last_error_code,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _article_sync_readiness_out(readiness) -> ArticleSyncReadinessOut:
    return ArticleSyncReadinessOut.model_validate(
        {
            "canSync": readiness.can_sync,
            "blockers": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "claimId": issue.claim_id,
                }
                for issue in readiness.blockers
            ],
            "warnings": [
                {
                    "code": issue.code,
                    "message": issue.message,
                    "claimId": issue.claim_id,
                }
                for issue in readiness.warnings
            ],
            "unresolvedClaimCount": readiness.unresolved_claim_count,
        }
    )


def _validate_wechat_sync_approval(job: PlatformPublishJob) -> None:
    package = job.publish_package if isinstance(job.publish_package, dict) else {}
    approval = job.approval_snapshot if isinstance(job.approval_snapshot, dict) else {}
    expected = {
        "actor_id": job.created_by_id,
        "account_id": job.account_id,
        "article_id": package.get("article_id"),
        "article_version_id": job.article_version_id,
        "conflict_strategy": package.get("conflict_strategy"),
        "expected_remote_hash": job.expected_remote_hash,
        "request_digest": job.request_digest,
    }
    if any(approval.get(key) != value for key, value in expected.items()) or not isinstance(
        approval.get("approved_at"), str
    ):
        raise PublishingServiceError(
            "WECHAT_DRAFT_APPROVAL_INVALID",
            "同步批准信息无效，请重新确认后再同步。",
            status_code=422,
        )


async def _append_wechat_sync_step(
    session: AsyncSession,
    job: PlatformPublishJob,
    step: str,
    phase: str,
    *,
    error_code: str | None = None,
) -> None:
    version = await session.get(Deliverable, job.article_version_id)
    if version is None or version.thread_id is None or version.turn_id is None:
        return
    labels = {
        "readiness": "检查文章版本",
        "capabilities": "检查公众号能力",
        "assets": "检查文章素材",
        "conflict": "检查远端草稿冲突",
        "sync": "同步微信公众号草稿",
    }
    payload: dict[str, object] = {
        "step": step,
        "title": labels[step],
        "status": phase,
        "metadata": {"category": "wechat_draft_sync"},
    }
    if error_code is not None:
        payload["error_code"] = error_code
    await append_turn_event(
        session,
        TurnEventScope(
            org_id=job.org_id,
            account_id=job.account_id,
            thread_id=version.thread_id,
            turn_id=version.turn_id,
            run_id=version.run_id,
            skill_run_id=version.skill_run_id,
        ),
        f"step.{phase}",
        payload,
        f"wechat-draft-sync:{job.id}:{step}:{phase}",
    )
    await session.commit()


async def _append_deliverable_sync_step(
    session: AsyncSession,
    version: Deliverable,
    *,
    org_id: int,
    account_id: int,
    idempotency_key: str,
    step: str,
    phase: str,
    error_code: str | None = None,
) -> None:
    if version.thread_id is None or version.turn_id is None:
        return
    payload: dict[str, object] = {
        "step": step,
        "title": "检查文章版本",
        "status": phase,
        "metadata": {"category": "wechat_draft_sync"},
    }
    if error_code is not None:
        payload["error_code"] = error_code
    await append_turn_event(
        session,
        TurnEventScope(
            org_id=org_id,
            account_id=account_id,
            thread_id=version.thread_id,
            turn_id=version.turn_id,
            run_id=version.run_id,
            skill_run_id=version.skill_run_id,
        ),
        f"step.{phase}",
        payload,
        f"wechat-draft-sync:{idempotency_key}:{step}:{phase}",
    )
    await session.commit()


def _is_retryable_external_error(error: BaseException) -> bool:
    if not bool(getattr(error, "retryable", False)):
        return False
    code = str(getattr(error, "error_code", "") or "").lower()
    is_rate_limit = code in {"429", "45009", "http_429"} or "rate" in code
    return not is_rate_limit or bool(getattr(error, "retry_after_seconds", None))


def _safe_external_service_error(error: BaseException) -> PublishingServiceError:
    retryable = _is_retryable_external_error(error)
    return PublishingServiceError(
        "WECHAT_DRAFT_EXTERNAL_RETRYABLE" if retryable else "WECHAT_DRAFT_EXTERNAL_FAILURE",
        "微信公众号服务暂时不可用，请稍后重试。"
        if retryable
        else "微信公众号拒绝了本次同步，请检查授权或内容后重试。",
        retryable=retryable,
        status_code=503 if retryable else 422,
    )


async def _commit_boundary_failure_without_intent(
    session: AsyncSession,
    job: PlatformPublishJob,
    *,
    error: BaseException,
    fallback_code: str,
) -> None:
    retryable = _is_retryable_external_error(error)
    job.status = (
        PlatformPublishJobStatus.WECHAT_QUEUED
        if retryable
        else PlatformPublishJobStatus.WECHAT_BLOCKED
    )
    job.last_error_code = str(getattr(error, "error_code", None) or fallback_code)[:120]
    job.last_error_message = "微信公众号连接或授权校验失败"
    await session.commit()


def _safe_capability_snapshot(snapshot: WechatCapabilitySnapshot) -> dict[str, Any]:
    return snapshot.model_dump(mode="json")


async def _load_frozen_asset(
    session: AsyncSession,
    job: PlatformPublishJob,
    fact: dict[str, Any],
) -> tuple[bytes, str]:
    material = await session.scalar(
        select(MaterialAsset).where(
            MaterialAsset.id == fact.get("material_id"),
            MaterialAsset.org_id == job.org_id,
            MaterialAsset.content_item_id == (job.publish_package or {}).get("article_id"),
            MaterialAsset.kind == "image",
            MaterialAsset.status == MaterialStatus.READY,
        )
    )
    if material is None or not material.local_path:
        raise PublishingServiceError(
            "WECHAT_ARTICLE_ASSET_UNAVAILABLE",
            "文章图片文件不可用。",
            status_code=422,
        )
    current = _frozen_image_fact(material)
    if current != fact:
        raise PublishingServiceError(
            "WECHAT_ARTICLE_ASSET_CHANGED",
            "已批准的文章图片发生变化。",
            status_code=409,
        )
    path = Path(settings.storage_local_dir).resolve() / material.local_path  # noqa: ASYNC240
    with path.open("rb") as source:
        return source.read(), str(fact["media_type"])


async def _commit_external_intent(
    session: AsyncSession,
    job: PlatformPublishJob,
    *,
    operation: str,
) -> None:
    intent_key = _sync_event_key(job, operation, "intent")
    result_key = _sync_event_key(job, operation, "result")
    prior_intent = await session.scalar(select(Event).where(Event.idempotency_key == intent_key))
    if prior_intent is not None:
        prior_result = await session.scalar(
            select(Event).where(Event.idempotency_key == result_key)
        )
        if prior_result is None:
            job.status = PlatformPublishJobStatus.WECHAT_RECONCILIATION_REQUIRED
            job.last_error_code = "WECHAT_DRAFT_RECONCILIATION_REQUIRED"
            job.last_error_message = "微信外部写入结果需要人工对账"
            await session.commit()
            raise PublishingServiceError(
                "WECHAT_DRAFT_RECONCILIATION_REQUIRED",
                "上一次微信写入结果不明确，请先人工对账。",
                status_code=409,
            )
        return
    session.add(
        Event(
            type="wechat.draft_sync.intent",
            org_id=job.org_id,
            account_id=job.account_id,
            content_item_id=int((job.publish_package or {})["article_id"]),
            idempotency_key=intent_key,
            payload={
                "operation": operation,
                "status": "committed",
                "job_id": job.id,
                "attempt": job.retry_count,
            },
        )
    )
    await session.commit()


async def _commit_external_result(
    session: AsyncSession,
    job: PlatformPublishJob,
    *,
    operation: str,
) -> None:
    session.add(
        Event(
            type="wechat.draft_sync.result",
            org_id=job.org_id,
            account_id=job.account_id,
            content_item_id=int((job.publish_package or {})["article_id"]),
            idempotency_key=_sync_event_key(job, operation, "result"),
            payload={
                "operation": operation,
                "status": "succeeded",
                "job_id": job.id,
                "attempt": job.retry_count,
            },
        )
    )
    await session.commit()


async def _commit_external_failure(
    session: AsyncSession,
    job: PlatformPublishJob,
    *,
    operation: str,
    error: WechatDraftIntegrationError,
) -> None:
    retryable = _is_retryable_external_error(error)
    result_key = _sync_event_key(job, operation, "result")
    session.add(
        Event(
            type="wechat.draft_sync.result",
            org_id=job.org_id,
            account_id=job.account_id,
            content_item_id=int((job.publish_package or {})["article_id"]),
            idempotency_key=result_key,
            payload={
                "operation": operation,
                "status": "failed",
                "retryable": retryable,
                "error_code": str(error.error_code or "integration_error")[:80],
                "job_id": job.id,
                "attempt": job.retry_count,
            },
        )
    )
    await session.commit()
    job.status = (
        PlatformPublishJobStatus.WECHAT_QUEUED
        if retryable
        else PlatformPublishJobStatus.WECHAT_BLOCKED
    )
    job.last_error_code = str(error.error_code or "WECHAT_DRAFT_EXTERNAL_FAILURE")[:120]
    job.last_error_message = "微信公众号外部写入失败"
    await session.commit()


def _sync_event_key(job: PlatformPublishJob, operation: str, phase: str) -> str:
    return hashlib.sha256(
        (
            f"wechat-draft-sync:{job.id}:{job.request_digest}:"
            f"attempt:{job.retry_count}:{operation}:{phase}"
        ).encode()
    ).hexdigest()


async def _guard_unresolved_sync_intent(
    session: AsyncSession,
    job: PlatformPublishJob,
) -> None:
    if job.status is not PlatformPublishJobStatus.WECHAT_RUNNING:
        return
    events = list(
        await session.scalars(
            select(Event).where(
                Event.org_id == job.org_id,
                Event.account_id == job.account_id,
                Event.content_item_id == int((job.publish_package or {})["article_id"]),
                Event.type.in_({"wechat.draft_sync.intent", "wechat.draft_sync.result"}),
            )
        )
    )
    relevant = [
        (event, event.payload)
        for event in events
        if isinstance(event.payload, dict) and event.payload.get("job_id") == job.id
    ]
    intent_keys = {
        (payload.get("attempt"), payload.get("operation"))
        for event, payload in relevant
        if event.type == "wechat.draft_sync.intent"
    }
    result_keys = {
        (payload.get("attempt"), payload.get("operation"))
        for event, payload in relevant
        if event.type == "wechat.draft_sync.result"
    }
    unresolved = any(
        event.type == "wechat.draft_sync.intent"
        and (payload.get("attempt"), payload.get("operation")) not in result_keys
        for event, payload in relevant
    )
    if unresolved:
        job.status = PlatformPublishJobStatus.WECHAT_RECONCILIATION_REQUIRED
        job.last_error_code = "WECHAT_DRAFT_RECONCILIATION_REQUIRED"
        job.last_error_message = "微信外部写入结果需要人工对账"
        await session.commit()
        raise PublishingServiceError(
            "WECHAT_DRAFT_RECONCILIATION_REQUIRED",
            "上一次微信写入结果不明确，请先人工对账。",
            status_code=409,
        )
    current_results = [
        payload
        for event, payload in relevant
        if event.type == "wechat.draft_sync.result" and payload.get("attempt") == job.retry_count
    ]
    failed_or_malformed_result = any(
        payload.get("status") == "failed"
        or payload.get("status") not in {"succeeded", "failed"}
        or not isinstance(payload.get("operation"), str)
        or (payload.get("attempt"), payload.get("operation")) not in intent_keys
        or (
            payload.get("status") == "failed"
            and (
                not isinstance(payload.get("retryable"), bool)
                or not isinstance(payload.get("error_code"), str)
            )
        )
        for payload in current_results
    )
    if failed_or_malformed_result:
        job.status = PlatformPublishJobStatus.WECHAT_RECONCILIATION_REQUIRED
        job.last_error_code = "WECHAT_DRAFT_RECONCILIATION_REQUIRED"
        job.last_error_message = "微信外部写入失败结果已记录，任务状态需要人工对账"
        await session.commit()
        raise PublishingServiceError(
            "WECHAT_DRAFT_RECONCILIATION_REQUIRED",
            "上一轮微信写入已记录失败结果，但任务收口中断，请先人工对账。",
            status_code=409,
        )
    raise PublishingServiceError(
        "WECHAT_DRAFT_SYNC_ALREADY_RUNNING",
        "微信草稿同步任务已由其他执行器接管。",
        status_code=409,
    )


def _media_suffix(media_type: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[media_type]


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
            deliverable_stream_clause(
                content_item_id=artifact.content_item_id,
                agent_code=artifact.agent_code,
                deliverable_type=artifact.type,
            ),
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
        job.status.value if isinstance(job.status, PlatformPublishJobStatus) else str(job.status)
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
                PlatformPublishJob.status == PlatformPublishJobStatus.PENDING_APPROVAL,
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
            reason=("publish_approval_granted" if approved else "publish_approval_rejected"),
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
        await session.get(AgentToolCall, job.tool_call_id) if job.tool_call_id is not None else None
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
    if integration is None or not integration.client_key or not integration.client_secret_ref:
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
