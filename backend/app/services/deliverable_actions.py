"""Durable, account-scoped execution of explicit deliverable actions."""

import hashlib
import json
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import accessible_account_ids
from app.models import (
    ContentItem,
    ContentScheduleEntry,
    Deliverable,
    DeliverableActionExecution,
    ShootTask,
    User,
)
from app.models.enums import DeliverableStatus, DeliverableType, WorkspaceRole
from app.schemas.deliverable_actions import (
    DeliverableActionExecutionOut,
    DeliverableActionRequest,
    DeliverableActionResourceOut,
)
from app.services.artifacts import ARTIFACT_ACTION_ROLES, get_artifact
from app.services.deliverable_action_registry import SERVER_ACTIONS, server_action_for


async def execute_deliverable_action(
    session: AsyncSession,
    user: User,
    *,
    artifact_id: int,
    action_code: str,
    idempotency_key: str,
    body: DeliverableActionRequest,
) -> DeliverableActionExecutionOut:
    configured_action = SERVER_ACTIONS.get(action_code)
    roles = configured_action.roles if configured_action is not None else ARTIFACT_ACTION_ROLES
    if action_code == "create_shoot_task" and body.assignee_id is not None:
        roles = frozenset({WorkspaceRole.LEAD, WorkspaceRole.OPERATOR})
    artifact, content, _ = await get_artifact(
        session,
        user,
        artifact_id,
        roles=roles,
    )
    fingerprint = _request_fingerprint(artifact_id, action_code, body)
    existing = await session.scalar(
        select(DeliverableActionExecution).where(
            DeliverableActionExecution.org_id == user.org_id,
            DeliverableActionExecution.requested_by_id == user.id,
            DeliverableActionExecution.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise _business_conflict(
                "idempotency_key_conflict",
                "这个操作标识已用于另一项请求，请重新操作",
            ) from None
        return _execution_out(existing, replayed=True)

    artifact_status = {
        DeliverableStatus.DRAFT: "draft",
        DeliverableStatus.PENDING_REVIEW: "ready_for_review",
        DeliverableStatus.APPROVED: "accepted",
        DeliverableStatus.REJECTED: "revision_requested",
        DeliverableStatus.SUPERSEDED: "superseded",
    }[artifact.status]
    definition = server_action_for(
        action_code,
        artifact_type=artifact.type.value,
        artifact_status=artifact_status,
    )
    if definition is None:
        raise _business_conflict("action_unavailable", "当前内容不能执行这项操作")
    if definition.requires_confirmation and not body.confirmed:
        raise _business_conflict(
            "confirmation_required",
            f"请确认后再{definition.label}",
        )
    if body.assignee_id is not None:
        assignee = await session.get(User, body.assignee_id)
        accessible_ids = (
            None
            if assignee is None or assignee.org_id != user.org_id
            else await accessible_account_ids(session, assignee)
        )
        if (
            assignee is None
            or assignee.org_id != user.org_id
            or (accessible_ids is not None and content.account_id not in accessible_ids)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "invalid_assignee",
                    "message": "请选择当前团队成员",
                    "retryable": False,
                },
            )

    locked_content_id = await session.scalar(
        select(ContentItem.id)
        .where(ContentItem.id == artifact.content_item_id)
        .with_for_update()
    )
    if locked_content_id is None:
        raise HTTPException(status_code=404, detail="内容不存在")
    latest_version = await session.scalar(
        select(Deliverable.version)
        .where(
            Deliverable.content_item_id == artifact.content_item_id,
            Deliverable.type == artifact.type,
        )
        .order_by(Deliverable.version.desc())
        .limit(1)
    )
    if artifact.status == DeliverableStatus.SUPERSEDED or artifact.version != latest_version:
        raise _business_conflict("content_version_updated", "内容版本已更新，请刷新后重试")

    execution = DeliverableActionExecution(
        org_id=user.org_id,
        account_id=content.account_id,
        requested_by_id=user.id,
        artifact_id=artifact.id,
        artifact_version=artifact.version,
        action_code=action_code,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        status="processing",
        confirmed_by_id=user.id if body.confirmed else None,
        confirmed_at=datetime.now(UTC) if body.confirmed else None,
        result_payload={},
    )
    session.add(execution)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        replay = await session.scalar(
            select(DeliverableActionExecution).where(
                DeliverableActionExecution.org_id == user.org_id,
                DeliverableActionExecution.requested_by_id == user.id,
                DeliverableActionExecution.idempotency_key == idempotency_key,
            )
        )
        if replay is None or replay.request_fingerprint != fingerprint:
            raise _business_conflict(
                "idempotency_key_conflict",
                "这个操作标识已用于另一项请求，请重新操作",
            ) from None
        return _execution_out(replay, replayed=True)

    if action_code == "create_shoot_task" and artifact.type == DeliverableType.VIDEO_SCRIPT:
        resource = ShootTask(
            org_id=user.org_id,
            account_id=content.account_id,
            content_item_id=content.id,
            source_artifact_id=artifact.id,
            source_artifact_version=artifact.version,
            created_by_id=user.id,
            assignee_id=body.assignee_id,
            title=f"拍摄：{content.title}",
            status="pending",
            due_at=body.due_at,
            note=body.note,
        )
        resource_type = "shoot_task"
        result_message = "拍摄任务已创建"
    elif action_code == "add_to_schedule" and artifact.type == DeliverableType.PUBLISH_CALENDAR:
        if body.scheduled_at is None or body.timezone is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "schedule_details_required",
                    "message": "请选择计划发布时间和时区",
                    "retryable": False,
                },
            )
        resource = ContentScheduleEntry(
            org_id=user.org_id,
            account_id=content.account_id,
            content_item_id=content.id,
            source_artifact_id=artifact.id,
            source_artifact_version=artifact.version,
            created_by_id=user.id,
            scheduled_at=body.scheduled_at,
            timezone=body.timezone,
            status="planned",
        )
        resource_type = "schedule_entry"
        result_message = "内容排期已创建"
    else:
        raise _business_conflict("action_unavailable", "当前内容不能执行这项操作")
    session.add(resource)
    await session.flush()
    execution.status = "succeeded"
    execution.resource_type = resource_type
    execution.resource_id = resource.id
    execution.result_payload = {"message": result_message}
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        replay = await session.scalar(
            select(DeliverableActionExecution).where(
                DeliverableActionExecution.org_id == user.org_id,
                DeliverableActionExecution.requested_by_id == user.id,
                DeliverableActionExecution.idempotency_key == idempotency_key,
            )
        )
        if replay is None or replay.request_fingerprint != fingerprint:
            raise _business_conflict(
                "idempotency_key_conflict",
                "这个操作标识已用于另一项请求，请重新操作",
            ) from None
        return _execution_out(replay, replayed=True)
    return _execution_out(execution, replayed=False)


def _request_fingerprint(
    artifact_id: int,
    action_code: str,
    body: DeliverableActionRequest,
) -> str:
    raw = json.dumps(
        {
            "artifact_id": artifact_id,
            "action_code": action_code,
            "body": body.model_dump(mode="json", exclude_none=True),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _execution_out(
    execution: DeliverableActionExecution,
    *,
    replayed: bool,
) -> DeliverableActionExecutionOut:
    resource = None
    if execution.resource_type and execution.resource_id:
        resource = DeliverableActionResourceOut(
            type=execution.resource_type,
            id=execution.resource_id,
        )
    return DeliverableActionExecutionOut(
        execution_id=execution.id,
        artifact_id=execution.artifact_id,
        artifact_version=execution.artifact_version,
        action_code=execution.action_code,
        status=execution.status,
        resource=resource,
        result=execution.result_payload or {},
        replayed=replayed,
    )


def _business_conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message, "retryable": False},
    )
