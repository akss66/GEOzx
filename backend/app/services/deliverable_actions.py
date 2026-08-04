"""Durable, account-scoped execution of explicit deliverable actions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.main_agent_runtime import require_main_agent_runtime_enabled
from app.core.workspace_access import accessible_account_ids, require_account_access
from app.models import (
    ContentItem,
    ContentScheduleEntry,
    Deliverable,
    DeliverableActionExecution,
    ShootTask,
    User,
)
from app.models.enums import DeliverableStatus, WorkspaceRole
from app.schemas.conversation import CreateConversationTurnRequest
from app.schemas.deliverable_actions import (
    AddToScheduleActionRequest,
    CreateShootTaskActionRequest,
    DeliverableActionExecutionOut,
    DeliverableActionRequest,
    DeliverableActionResourceOut,
    GenerateNextIterationActionRequest,
    RequestRevisionActionRequest,
)
from app.services.agent_runs import enqueue_agent_runtime
from app.services.artifacts import (
    create_artifact_revision_record,
    get_artifact,
    validate_complete_artifact_payload,
)
from app.services.conversation_submission import prepare_conversation_turn_submission
from app.services.conversations import get_conversation_thread
from app.services.deliverable_action_registry import SERVER_ACTIONS, server_action_for


@dataclass(frozen=True)
class ActionContext:
    session: AsyncSession
    user: User
    artifact: Deliverable
    content: ContentItem
    execution: DeliverableActionExecution
    body: BaseModel


@dataclass(frozen=True)
class ActionResult:
    status: str
    resource_type: str
    resource_id: int
    result_payload: dict
    enqueue_run_id: int | None = None


ActionHandler = Callable[[ActionContext], Awaitable[ActionResult]]


async def execute_deliverable_action(
    session: AsyncSession,
    user: User,
    *,
    artifact_id: int,
    action_code: str,
    idempotency_key: str,
    body: DeliverableActionRequest,
) -> DeliverableActionExecutionOut:
    # Read authorization deliberately precedes all idempotency disclosure.
    artifact, content, _ = await get_artifact(session, user, artifact_id)
    configured_action = SERVER_ACTIONS.get(action_code)
    if configured_action is None:
        raise _business_conflict("action_unavailable", "当前内容不能执行这项操作")
    validated_body = _validate_action_body(configured_action.request_model, body)
    fingerprint = _request_fingerprint(artifact_id, action_code, validated_body)
    existing = await _find_execution(session, user, idempotency_key)
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise _business_conflict(
                "idempotency_key_conflict",
                "这个操作标识已用于另一项请求，请重新操作",
            )
        if existing.status == "processing":
            raise _business_conflict(
                "action_in_progress",
                "该操作正在处理中，请稍后重试",
                retryable=True,
            )
        return _execution_out(existing, replayed=True)

    artifact_status = _artifact_status(artifact.status)
    definition = server_action_for(
        action_code,
        artifact_type=artifact.type.value,
        deliverable_type=artifact.type,
        artifact_status=artifact_status,
    )
    if definition is None:
        raise _business_conflict("action_unavailable", "当前内容不能执行这项操作")

    roles = definition.roles
    if (
        action_code == "create_shoot_task"
        and isinstance(validated_body, CreateShootTaskActionRequest)
        and validated_body.assignee_id is not None
    ):
        roles = frozenset({WorkspaceRole.LEAD, WorkspaceRole.OPERATOR})
    await require_account_access(session, user, content.account_id, roles=roles)

    if definition.requires_confirmation and not getattr(validated_body, "confirmed", False):
        raise _business_conflict(
            "confirmation_required",
            f"请确认后再{definition.label}",
        )
    if action_code == "create_shoot_task":
        await _validate_assignee(session, user, content, validated_body)
    if action_code == "generate_next_iteration":
        try:
            require_main_agent_runtime_enabled()
        except HTTPException as exc:
            raise _business_conflict(
                "action_unavailable",
                "下一轮运行能力当前不可用",
            ) from exc

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
        raise _business_conflict(
            "content_version_updated",
            "内容版本已更新，请刷新后重试",
        )

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
        confirmed_by_id=(
            user.id if getattr(validated_body, "confirmed", False) else None
        ),
        confirmed_at=(
            datetime.now(UTC)
            if getattr(validated_body, "confirmed", False)
            else None
        ),
        result_payload={},
    )
    try:
        session.add(execution)
        await session.flush()
    except IntegrityError:
        await session.rollback()
        replay = await _find_execution(session, user, idempotency_key)
        if replay is None or replay.request_fingerprint != fingerprint:
            raise _business_conflict(
                "idempotency_key_conflict",
                "这个操作标识已用于另一项请求，请重新操作",
            ) from None
        if replay.status == "processing":
            raise _business_conflict(
                "action_in_progress",
                "该操作正在处理中，请稍后重试",
                retryable=True,
            ) from None
        return _execution_out(replay, replayed=True)

    handler = ACTION_HANDLERS[action_code]
    try:
        result = await handler(
            ActionContext(
                session=session,
                user=user,
                artifact=artifact,
                content=content,
                execution=execution,
                body=validated_body,
            )
        )
        execution.status = result.status
        execution.resource_type = result.resource_type
        execution.resource_id = result.resource_id
        execution.result_payload = result.result_payload
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    if result.enqueue_run_id is not None:
        await enqueue_agent_runtime(run_id=result.enqueue_run_id)
    return _execution_out(execution, replayed=False)


async def _create_shoot_task(context: ActionContext) -> ActionResult:
    body = CreateShootTaskActionRequest.model_validate(context.body)
    resource = ShootTask(
        org_id=context.user.org_id,
        account_id=context.content.account_id,
        content_item_id=context.content.id,
        source_artifact_id=context.artifact.id,
        source_artifact_version=context.artifact.version,
        created_by_id=context.user.id,
        assignee_id=body.assignee_id,
        title=f"拍摄：{context.content.title}",
        status="pending",
        due_at=body.due_at,
        note=body.note,
    )
    context.session.add(resource)
    await context.session.flush()
    return ActionResult(
        status="succeeded",
        resource_type="shoot_task",
        resource_id=resource.id,
        result_payload={"message": "拍摄任务已创建"},
    )


async def _add_to_schedule(context: ActionContext) -> ActionResult:
    body = AddToScheduleActionRequest.model_validate(context.body)
    resource = ContentScheduleEntry(
        org_id=context.user.org_id,
        account_id=context.content.account_id,
        content_item_id=context.content.id,
        source_artifact_id=context.artifact.id,
        source_artifact_version=context.artifact.version,
        created_by_id=context.user.id,
        scheduled_at=body.scheduled_at,
        timezone=body.timezone,
        status="planned",
    )
    context.session.add(resource)
    await context.session.flush()
    return ActionResult(
        status="succeeded",
        resource_type="schedule_entry",
        resource_id=resource.id,
        result_payload={"message": "内容排期已创建"},
    )


async def _request_revision(context: ActionContext) -> ActionResult:
    body = RequestRevisionActionRequest.model_validate(context.body)
    validated_payload = validate_complete_artifact_payload(
        context.artifact.type,
        body.payload,
    )
    revision, _, _ = await create_artifact_revision_record(
        context.session,
        context.user,
        artifact_id=context.artifact.id,
        payload=validated_payload,
        note=body.note,
    )
    return ActionResult(
        status="succeeded",
        resource_type="artifact",
        resource_id=revision.id,
        result_payload={
            "artifact_id": revision.id,
            "artifact_version": revision.version,
            "message": "修改版本已保存",
        },
    )


async def _generate_next_iteration(context: ActionContext) -> ActionResult:
    GenerateNextIterationActionRequest.model_validate(context.body)
    if context.artifact.thread_id is None:
        raise _business_conflict("action_unavailable", "当前成果没有可继续的对话")
    thread = await get_conversation_thread(
        context.session,
        context.user,
        context.artifact.thread_id,
    )
    if thread.account_id != context.content.account_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="成果不存在")
    client_message_id = f"deliverable-action:{context.execution.id}"
    request = CreateConversationTurnRequest(
        client_message_id=client_message_id,
        message=(
            f"基于已确认的复盘成果 #{context.artifact.id} "
            f"（版本 {context.artifact.version}）准备下一轮运营计划。"
        ),
        requested_skill_code="operation_iteration",
        execution_preference="FORMAL_TASK",
        attachment_ids=[],
    )
    prepared = await prepare_conversation_turn_submission(
        context.session,
        context.user,
        thread,
        request,
        [],
        trusted_structured_input={
            "confirmed_review_artifact_id": context.artifact.id,
            "cycle_days": 7,
        },
    )
    return ActionResult(
        status="queued",
        resource_type="conversation_turn",
        resource_id=prepared.turn.id,
        result_payload={
            "run_id": prepared.run.id,
            "thread_id": thread.id,
            "message": "下一轮运营任务已排队",
        },
        enqueue_run_id=prepared.run.id if prepared.claimed else None,
    )


ACTION_HANDLERS: dict[str, ActionHandler] = {
    "create_shoot_task": _create_shoot_task,
    "add_to_schedule": _add_to_schedule,
    "request_revision": _request_revision,
    "generate_next_iteration": _generate_next_iteration,
}
if set(ACTION_HANDLERS) != set(SERVER_ACTIONS):
    raise RuntimeError("deliverable action registry and handler map must be closed")


async def _validate_assignee(
    session: AsyncSession,
    user: User,
    content: ContentItem,
    body: BaseModel,
) -> None:
    request = CreateShootTaskActionRequest.model_validate(body)
    if request.assignee_id is None:
        return
    assignee = await session.get(User, request.assignee_id)
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


async def _find_execution(
    session: AsyncSession,
    user: User,
    idempotency_key: str,
) -> DeliverableActionExecution | None:
    return await session.scalar(
        select(DeliverableActionExecution).where(
            DeliverableActionExecution.org_id == user.org_id,
            DeliverableActionExecution.requested_by_id == user.id,
            DeliverableActionExecution.idempotency_key == idempotency_key,
        )
    )


def _artifact_status(value: DeliverableStatus) -> str:
    return {
        DeliverableStatus.DRAFT: "draft",
        DeliverableStatus.PENDING_REVIEW: "ready_for_review",
        DeliverableStatus.APPROVED: "accepted",
        DeliverableStatus.REJECTED: "revision_requested",
        DeliverableStatus.SUPERSEDED: "superseded",
    }[value]


def _validate_action_body(
    request_model: type[BaseModel],
    body: DeliverableActionRequest,
) -> BaseModel:
    try:
        return request_model.model_validate(
            body.model_dump(mode="python", exclude_unset=True)
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_action_request",
                "message": "操作参数不符合当前动作要求",
                "retryable": False,
                "errors": exc.errors(include_url=False),
            },
        ) from exc


def _request_fingerprint(
    artifact_id: int,
    action_code: str,
    body: BaseModel,
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


def _business_conflict(
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message, "retryable": retryable},
    )


__all__ = ["ACTION_HANDLERS", "execute_deliverable_action"]
