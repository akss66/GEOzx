"""Additive Thread and Turn endpoints for the main-Agent V2 runtime."""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.main_agent_runtime import require_main_agent_runtime_enabled
from app.db import get_session
from app.models import (
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    SkillRun,
    ToolExecutionAttempt,
)
from app.schemas.conversation import (
    ConversationAgentRunOut,
    ConversationApprovalOut,
    ConversationDeletionSummary,
    ConversationExecutionSummaryOut,
    ConversationThreadListOut,
    ConversationThreadOut,
    ConversationThreadSummaryOut,
    ConversationTurnOut,
    CreateConversationThreadRequest,
    CreateConversationTurnRequest,
    TurnSubmissionOut,
    sanitize_conversation_projection,
)
from app.services.agent_runs import enqueue_agent_runtime
from app.services.attachments import resolve_attachment_contexts
from app.services.conversation_submission import prepare_conversation_turn_submission
from app.services.conversations import (
    create_conversation_thread,
    delete_conversation_thread,
    get_conversation_thread,
    list_conversation_threads,
)
from app.services.turn_steering import apply_turn_steering, resolve_turn_steering

router = APIRouter(prefix="/brain", tags=["brain-conversations"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
logger = logging.getLogger(__name__)
_DISPATCH_DEFERRED_MESSAGE = "任务已保存，调度暂时延迟，系统将自动恢复。"


def _require_v2_rollout_access(user: CurrentUser) -> None:
    del user
    require_main_agent_runtime_enabled()


def _turn_out(
    turn: ConversationTurn,
    projections: list[dict] | None = None,
) -> ConversationTurnOut:
    return ConversationTurnOut.model_validate(
        {
            "id": turn.id,
            "thread_id": turn.thread_id,
            "org_id": turn.org_id,
            "created_by_id": turn.created_by_id,
            "client_message_id": turn.client_message_id,
            "user_input": turn.user_input,
            "target_turn_id": turn.target_turn_id,
            "steering_mode": turn.steering_mode,
            "assistant_response": turn.assistant_response,
            "intent": _safe_turn_intent(turn.intent),
            "status": turn.status,
            "route_ms": turn.route_ms,
            "first_token_ms": turn.first_token_ms,
            "completion_ms": turn.completion_ms,
            "total_ms": turn.total_ms,
            "model_call_count": turn.model_call_count,
            "tool_call_count": turn.tool_call_count,
            "projections": _bind_projections_to_turn(turn.id, projections),
            "created_at": turn.created_at,
            "updated_at": turn.updated_at,
        }
    )


def _bind_projections_to_turn(
    turn_id: int,
    projections: list[dict] | None,
) -> list[dict]:
    safe: list[dict] = []
    for projection in projections or []:
        if not isinstance(projection, dict):
            continue
        sanitized = sanitize_conversation_projection({**projection, "turn_id": turn_id})
        if sanitized is not None:
            safe.append(sanitized)
    return safe


async def _thread_out(
    session: AsyncSession,
    thread: ConversationThread,
    turns: list[ConversationTurn],
) -> ConversationThreadOut:
    return ConversationThreadOut(
        id=thread.id,
        org_id=thread.org_id,
        created_by_id=thread.created_by_id,
        client_id=thread.client_id,
        project_id=thread.project_id,
        account_id=thread.account_id,
        title=thread.title,
        turns=[_turn_out(turn, await _turn_projections(session, turn)) for turn in turns],
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


async def _turn_projections(
    session: AsyncSession,
    turn: ConversationTurn,
) -> list[dict]:
    payload = await session.scalar(
        select(AgentRun.result_payload)
        .where(
            AgentRun.org_id == turn.org_id,
            AgentRun.thread_id == turn.thread_id,
            AgentRun.turn_id == turn.id,
        )
        .order_by(AgentRun.id.desc())
        .limit(1)
    )
    raw_projections = (
        list(payload.get("projections", []))
        if isinstance(payload, dict) and isinstance(payload.get("projections"), list)
        else []
    )
    result_payload_types = {
        "progress",
        "expert",
        "artifact",
        "account_data",
        "execution_blocked",
    }
    projections = [
        safe
        for projection in raw_projections
        if isinstance(projection, dict) and projection.get("type") in result_payload_types
        if (safe := sanitize_conversation_projection(projection)) is not None
    ]
    projections.extend(await _approval_projections(session, turn))
    execution_summary = await _execution_summary_projection(session, turn)
    if execution_summary is not None:
        projections.append(execution_summary)
    return projections


async def _approval_projections(
    session: AsyncSession,
    turn: ConversationTurn,
) -> list[dict]:
    """Restore pending approvals without exposing ToolCall meta or raw payloads."""

    approvals = list(
        await session.scalars(
            select(AgentToolCall)
            .where(
                AgentToolCall.org_id == turn.org_id,
                AgentToolCall.thread_id == turn.thread_id,
                AgentToolCall.turn_id == turn.id,
                AgentToolCall.requires_human_confirmation.is_(True),
                AgentToolCall.status == "waiting_approval",
            )
            .order_by(AgentToolCall.id)
        )
    )
    return [
        {
            "type": "approval",
            "approval": ConversationApprovalOut.model_validate(approval).model_dump(mode="json"),
        }
        for approval in approvals
    ]


async def _execution_summary_projection(
    session: AsyncSession,
    turn: ConversationTurn,
) -> dict | None:
    """Expose business provenance while keeping raw execution traces collapsed."""

    run = await session.scalar(
        select(AgentRun)
        .where(
            AgentRun.org_id == turn.org_id,
            AgentRun.thread_id == turn.thread_id,
            AgentRun.turn_id == turn.id,
        )
        .order_by(AgentRun.id.desc())
        .limit(1)
    )
    skill_run = await session.scalar(
        select(SkillRun)
        .where(
            SkillRun.org_id == turn.org_id,
            SkillRun.thread_id == turn.thread_id,
            SkillRun.turn_id == turn.id,
        )
        .order_by(SkillRun.id.desc())
        .limit(1)
    )
    invocations = list(
        await session.scalars(
            select(AgentInvocation)
            .where(
                AgentInvocation.thread_id == turn.thread_id,
                AgentInvocation.turn_id == turn.id,
            )
            .order_by(AgentInvocation.id)
        )
    )
    tool_calls = list(
        await session.scalars(
            select(AgentToolCall)
            .where(
                AgentToolCall.org_id == turn.org_id,
                AgentToolCall.thread_id == turn.thread_id,
                AgentToolCall.turn_id == turn.id,
            )
            .order_by(AgentToolCall.id)
        )
    )
    deliverables = list(
        await session.scalars(
            select(Deliverable)
            .where(
                Deliverable.thread_id == turn.thread_id,
                Deliverable.turn_id == turn.id,
            )
            .order_by(Deliverable.id)
        )
    )
    if skill_run is None and not invocations and not tool_calls:
        return None
    safe_intent = _safe_turn_intent(turn.intent)
    attempt_counts: dict[int, int] = {}
    if tool_calls:
        attempt_counts = {
            tool_call_id: attempt_count
            for tool_call_id, attempt_count in (
                await session.execute(
                    select(
                        ToolExecutionAttempt.tool_call_id,
                        func.count(ToolExecutionAttempt.id),
                    )
                    .where(
                        ToolExecutionAttempt.tool_call_id.in_(
                            [tool_call.id for tool_call in tool_calls]
                        )
                    )
                    .group_by(ToolExecutionAttempt.tool_call_id)
                )
            ).all()
        }
    retry_counts = {
        tool_call.id: max(0, attempt_counts.get(tool_call.id, 0) - 1) for tool_call in tool_calls
    }
    summary = ConversationExecutionSummaryOut(
        run_id=run.id if run is not None else None,
        mode=safe_intent.mode if safe_intent is not None else None,
        route_source=(safe_intent.route_source if safe_intent is not None else "system"),
        skill_code=skill_run.skill_code if skill_run is not None else None,
        skill_version=skill_run.skill_version if skill_run is not None else None,
        skill_run_id=skill_run.id if skill_run is not None else None,
        status=skill_run.status if skill_run is not None else None,
        quality_score=(
            float(skill_run.quality_score)
            if skill_run is not None and skill_run.quality_score is not None
            else None
        ),
        experts=[
            {
                "id": invocation.id,
                "agent_code": invocation.agent_code.value,
                "agent_name": invocation.agent_name,
                "status": invocation.status.value,
                "attempt": invocation.attempt,
                "duration_ms": _duration_ms(
                    invocation.started_at,
                    invocation.finished_at,
                ),
            }
            for invocation in invocations
        ],
        tools=[
            {
                "id": tool_call.id,
                "tool_code": tool_call.tool_code,
                "tool_name": tool_call.tool_name,
                "status": tool_call.status,
                "duration_ms": tool_call.latency_ms,
                "retry_count": retry_counts[tool_call.id],
                "requires_confirmation": tool_call.requires_human_confirmation,
                "side_effect_level": tool_call.side_effect_level,
            }
            for tool_call in tool_calls
        ],
        error_code=_public_error_code(run.error_code if run is not None else None),
        recovery_action=_recovery_action(run.error_code if run is not None else None),
        artifact_ids=[deliverable.id for deliverable in deliverables],
        evidence_ids=_evidence_ids(deliverables),
    )
    return summary.model_dump(mode="json")


def _safe_turn_intent(raw: dict | None):
    from app.schemas.conversation import ConversationTurnIntentOut

    if not isinstance(raw, dict):
        return None
    mode = raw.get("mode")
    mode = mode if isinstance(mode, str) and len(mode) <= 40 else None
    skill_code = raw.get("skill_code")
    skill_code = skill_code if isinstance(skill_code, str) and len(skill_code) <= 120 else None
    intent = raw.get("intent")
    reason = raw.get("reason")
    if isinstance(reason, str) and (
        reason.startswith("fast_route:") or reason.startswith("deterministic_")
    ):
        source = "deterministic"
    elif isinstance(reason, str) and reason.startswith("Resume the persisted"):
        source = "recovery"
    elif intent in {"explicit_skill", "account_data_query"}:
        source = "explicit"
    elif intent in {"intelligence_unavailable"}:
        source = "system"
    else:
        source = "model"
    return ConversationTurnIntentOut(
        mode=mode,
        route_source=source,
        skill_code=skill_code,
    )


def _duration_ms(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    return max(0, round((finished_at - started_at).total_seconds() * 1000))


def _public_error_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if not normalized or len(normalized) > 120:
        return "RUNTIME_FAILED"
    if not all(character.isalnum() or character == "_" for character in normalized):
        return "RUNTIME_FAILED"
    return normalized


_RECOVERY_ACTIONS = {
    "INTELLIGENCE_UNAVAILABLE": "稍后重试，或明确说明只查询数据还是创建正式任务。",
    "ANSWER_MODEL_UNAVAILABLE": "稍后重试本轮对话。",
    "SKILL_EXECUTOR_UNAVAILABLE": "稍后重试，或改为查询账号数据。",
    "QUERY_TOOL_UNAVAILABLE": "检查账号授权和数据同步状态后重试。",
    "TOOL_RESULT_SCOPE_MISMATCH": "刷新当前账号后重试。",
    "RUN_CANCELLED": "调整要求后重新发起。",
}
_DEFAULT_RECOVERY_ACTION = "请稍后重试；持续失败时联系管理员并提供消息编号。"


def _recovery_action(error_code: str | None) -> str | None:
    public_code = _public_error_code(error_code)
    if public_code is None:
        return None
    return _RECOVERY_ACTIONS.get(public_code, _DEFAULT_RECOVERY_ACTION)


def _evidence_ids(deliverables: list[Deliverable]) -> list[int]:
    values: list[int] = []
    for deliverable in deliverables:
        refs = deliverable.payload.get("evidence_refs")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            ref_id = ref.get("id") if isinstance(ref, dict) else None
            if (
                isinstance(ref_id, int)
                and not isinstance(ref_id, bool)
                and ref_id > 0
                and ref_id not in values
            ):
                values.append(ref_id)
    return values


async def _ordered_turns(
    session: AsyncSession,
    thread: ConversationThread,
) -> list[ConversationTurn]:
    return list(
        await session.scalars(
            select(ConversationTurn)
            .where(
                ConversationTurn.thread_id == thread.id,
                ConversationTurn.org_id == thread.org_id,
            )
            .order_by(ConversationTurn.id)
        )
    )


@router.post(
    "/conversations",
    response_model=ConversationThreadOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    body: CreateConversationThreadRequest,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationThreadOut:
    _require_v2_rollout_access(user)
    thread = await create_conversation_thread(session, user, body)
    await session.commit()
    await session.refresh(thread)
    return await _thread_out(session, thread, [])


@router.get(
    "/conversations",
    response_model=ConversationThreadListOut,
)
async def list_threads(
    account_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationThreadListOut:
    _require_v2_rollout_access(user)
    rows = await list_conversation_threads(
        session,
        user,
        account_id=account_id,
    )
    return ConversationThreadListOut(
        data=[
            ConversationThreadSummaryOut(
                id=row.thread.id,
                account_id=row.thread.account_id,
                title=row.thread.title,
                turn_count=row.turn_count,
                last_message=row.last_message,
                created_at=row.thread.created_at,
                updated_at=row.thread.updated_at,
            )
            for row in rows
        ]
    )


@router.get(
    "/conversations/{thread_id}",
    response_model=ConversationThreadOut,
)
async def get_thread(
    thread_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationThreadOut:
    _require_v2_rollout_access(user)
    thread = await get_conversation_thread(session, user, thread_id)
    return await _thread_out(
        session,
        thread,
        await _ordered_turns(session, thread),
    )


@router.delete(
    "/conversations/{thread_id}",
    response_model=ConversationDeletionSummary,
)
async def delete_thread(
    thread_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationDeletionSummary:
    _require_v2_rollout_access(user)
    return await delete_conversation_thread(session, user, thread_id)


@router.post(
    "/conversations/{thread_id}/turns",
    response_model=TurnSubmissionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_turn(
    thread_id: int,
    body: CreateConversationTurnRequest,
    user: CurrentUser,
    session: SessionDep,
) -> TurnSubmissionOut:
    _require_v2_rollout_access(user)
    thread = await get_conversation_thread(session, user, thread_id)
    resolved_steering = await resolve_turn_steering(session, user, thread, body)
    attachment_contexts = await resolve_attachment_contexts(
        session,
        user=user,
        thread=thread,
        attachment_ids=body.attachment_ids,
    )
    prepared = await prepare_conversation_turn_submission(
        session,
        user,
        thread,
        body,
        attachment_contexts,
        steering_decision=resolved_steering.decision,
    )
    should_enqueue = await apply_turn_steering(
        session,
        thread,
        prepared.turn,
        prepared.run,
        resolved_steering,
    )
    await session.commit()
    turn = prepared.turn
    run = prepared.run
    dispatch_deferred = False
    if should_enqueue and run.status == "queued":
        try:
            await enqueue_agent_runtime(run_id=run.id)
        except Exception:  # noqa: BLE001 - durable queued state is recoverable
            dispatch_deferred = True
            logger.warning(
                "Conversation run dispatch deferred",
                extra={"run_id": run.id, "thread_id": thread.id},
                exc_info=True,
            )
    await session.refresh(turn)
    await session.refresh(run)
    projections = await _turn_projections(session, turn)
    turn_out = _turn_out(turn, projections)
    return TurnSubmissionOut(
        turn=turn_out,
        run=ConversationAgentRunOut.model_validate(run),
        task_id=run.task_id,
        steering_explanation=resolved_steering.decision.explanation,
        dispatch_deferred=dispatch_deferred,
        dispatch_message=_DISPATCH_DEFERRED_MESSAGE if dispatch_deferred else None,
        projections=turn_out.projections,
    )


@router.get(
    "/turns/{turn_id}",
    response_model=ConversationTurnOut,
)
async def get_turn(
    turn_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationTurnOut:
    _require_v2_rollout_access(user)
    turn = await session.scalar(
        select(ConversationTurn).where(
            ConversationTurn.id == turn_id,
            ConversationTurn.org_id == user.org_id,
        )
    )
    if turn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation turn not found",
        )
    await get_conversation_thread(session, user, turn.thread_id)
    return _turn_out(turn, await _turn_projections(session, turn))
