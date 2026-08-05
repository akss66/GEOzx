"""Additive Thread and Turn endpoints for the main-Agent V2 runtime."""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, TypeVar

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
    TurnInterrupt,
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
    ConversationTurnRecoveryContextOut,
    CreateConversationThreadRequest,
    CreateConversationTurnRequest,
    TurnSubmissionOut,
    sanitize_conversation_projection,
)
from app.schemas.turn_interrupt import TurnInterruptOut
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
_ProjectionRow = TypeVar("_ProjectionRow")
_DISPATCH_DEFERRED_MESSAGE = "任务已保存，调度暂时延迟，系统将自动恢复。"


def _require_v2_rollout_access(user: CurrentUser) -> None:
    del user
    require_main_agent_runtime_enabled()


def _turn_out(
    turn: ConversationTurn,
    projections: list[dict] | None = None,
    pending_interrupt: TurnInterrupt | None = None,
    recovery_context: ConversationTurnRecoveryContextOut | None = None,
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
            "recovery_context": recovery_context,
            "status": turn.status,
            "route_ms": turn.route_ms,
            "first_token_ms": turn.first_token_ms,
            "completion_ms": turn.completion_ms,
            "total_ms": turn.total_ms,
            "model_call_count": turn.model_call_count,
            "tool_call_count": turn.tool_call_count,
            "pending_interrupt": (
                TurnInterruptOut.model_validate(pending_interrupt)
                if pending_interrupt is not None
                else None
            ),
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
    pending_by_turn = await _pending_interrupts_by_turn(
        session,
        tuple(turn.id for turn in turns),
    )
    latest_run_by_turn = await _latest_agent_runs_by_turn(session, turns)
    projections_by_turn = await _turn_projections_by_turn(session, turns)
    return ConversationThreadOut(
        id=thread.id,
        org_id=thread.org_id,
        created_by_id=thread.created_by_id,
        client_id=thread.client_id,
        project_id=thread.project_id,
        account_id=thread.account_id,
        title=thread.title,
        turns=[
            _turn_out(
                turn,
                projections_by_turn.get(turn.id, []),
                pending_by_turn.get(turn.id),
                _recovery_context(turn, latest_run_by_turn.get(turn.id)),
            )
            for turn in turns
        ],
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


async def _latest_agent_runs_by_turn(
    session: AsyncSession,
    turns: list[ConversationTurn],
) -> dict[int, AgentRun]:
    if not turns:
        return {}
    rows = list(
        await session.scalars(
            select(AgentRun)
            .where(AgentRun.turn_id.in_(tuple(turn.id for turn in turns)))
            .order_by(AgentRun.turn_id, AgentRun.id.desc())
        )
    )
    return _latest_by_turn(rows, lambda row: row.turn_id or 0)


def _recovery_context(
    turn: ConversationTurn,
    run: AgentRun | None,
) -> ConversationTurnRecoveryContextOut | None:
    if run is None or not isinstance(run.request_payload, dict):
        return None
    raw_skill_code = run.request_payload.get("requested_skill_code")
    requested_skill_code = (
        raw_skill_code.strip()
        if isinstance(raw_skill_code, str) and 0 < len(raw_skill_code.strip()) <= 120
        else None
    )
    safe_intent = _safe_turn_intent(turn.intent)
    routed_skill_code = safe_intent.skill_code if safe_intent is not None else None
    attachment_ids: list[int] = []
    raw_attachment_ids = run.request_payload.get("attachment_ids")
    if isinstance(raw_attachment_ids, list):
        for value in raw_attachment_ids:
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value > 0
                and value not in attachment_ids
            ):
                attachment_ids.append(value)
    return ConversationTurnRecoveryContextOut(
        requested_skill_code=requested_skill_code,
        routed_skill_code=routed_skill_code,
        attachment_ids=attachment_ids,
    )


async def _pending_interrupts_by_turn(
    session: AsyncSession,
    turn_ids: tuple[int, ...],
) -> dict[int, TurnInterrupt]:
    if not turn_ids:
        return {}
    rows = list(
        await session.scalars(
            select(TurnInterrupt)
            .where(
                TurnInterrupt.turn_id.in_(turn_ids),
                TurnInterrupt.status == "pending",
            )
            .order_by(TurnInterrupt.id)
        )
    )
    return {row.turn_id: row for row in rows}


async def _turn_projections(
    session: AsyncSession,
    turn: ConversationTurn,
) -> list[dict]:
    return (await _turn_projections_by_turn(session, [turn])).get(turn.id, [])


async def _turn_projections_by_turn(
    session: AsyncSession,
    turns: list[ConversationTurn],
) -> dict[int, list[dict]]:
    """Load every projection family in constant queries for a Turn snapshot."""

    if not turns:
        return {}
    turn_ids = tuple(turn.id for turn in turns)
    runs = list(
        await session.scalars(
            select(AgentRun)
            .where(AgentRun.turn_id.in_(turn_ids))
            .order_by(AgentRun.turn_id, AgentRun.id.desc())
        )
    )
    skill_runs = list(
        await session.scalars(
            select(SkillRun)
            .where(SkillRun.turn_id.in_(turn_ids))
            .order_by(SkillRun.turn_id, SkillRun.id.desc())
        )
    )
    invocations = list(
        await session.scalars(
            select(AgentInvocation)
            .where(AgentInvocation.turn_id.in_(turn_ids))
            .order_by(AgentInvocation.turn_id, AgentInvocation.id)
        )
    )
    tool_calls = list(
        await session.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.turn_id.in_(turn_ids))
            .order_by(AgentToolCall.turn_id, AgentToolCall.id)
        )
    )
    deliverables = list(
        await session.scalars(
            select(Deliverable)
            .where(Deliverable.turn_id.in_(turn_ids))
            .order_by(Deliverable.turn_id, Deliverable.id)
        )
    )
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

    latest_run_by_turn = _latest_by_turn(runs, lambda row: row.turn_id or 0)
    latest_skill_run_by_turn = _latest_by_turn(skill_runs, lambda row: row.turn_id or 0)
    invocations_by_turn = _group_by_turn(invocations, lambda row: row.turn_id or 0)
    tool_calls_by_turn = _group_by_turn(tool_calls, lambda row: row.turn_id or 0)
    deliverables_by_turn = _group_by_turn(deliverables, lambda row: row.turn_id or 0)
    projections_by_turn: dict[int, list[dict]] = {}
    result_payload_types = {
        "progress",
        "expert",
        "artifact",
        "account_data",
        "execution_blocked",
    }
    for turn in turns:
        run = latest_run_by_turn.get(turn.id)
        payload = run.result_payload if run is not None else None
        raw_projections = (
            list(payload.get("projections", []))
            if isinstance(payload, dict) and isinstance(payload.get("projections"), list)
            else []
        )
        projections = [
            safe
            for projection in raw_projections
            if isinstance(projection, dict) and projection.get("type") in result_payload_types
            if (safe := sanitize_conversation_projection(projection)) is not None
        ]
        turn_tool_calls = tool_calls_by_turn.get(turn.id, [])
        projections.extend(
            {
                "type": "approval",
                "approval": ConversationApprovalOut.model_validate(approval).model_dump(
                    mode="json"
                ),
            }
            for approval in turn_tool_calls
            if approval.requires_human_confirmation
            and approval.status == "waiting_approval"
        )
        execution_summary = _execution_summary_from_loaded(
            turn,
            run=run,
            skill_run=latest_skill_run_by_turn.get(turn.id),
            invocations=invocations_by_turn.get(turn.id, []),
            tool_calls=turn_tool_calls,
            deliverables=deliverables_by_turn.get(turn.id, []),
            attempt_counts=attempt_counts,
        )
        if execution_summary is not None:
            projections.append(execution_summary)
        projections_by_turn[turn.id] = projections
    return projections_by_turn


def _latest_by_turn(
    rows: list[_ProjectionRow],
    turn_id: Callable[[_ProjectionRow], int],
) -> dict[int, _ProjectionRow]:
    latest: dict[int, _ProjectionRow] = {}
    for row in rows:
        latest.setdefault(turn_id(row), row)
    return latest


def _group_by_turn(
    rows: list[_ProjectionRow],
    turn_id: Callable[[_ProjectionRow], int],
) -> dict[int, list[_ProjectionRow]]:
    grouped: dict[int, list[_ProjectionRow]] = {}
    for row in rows:
        grouped.setdefault(turn_id(row), []).append(row)
    return grouped


def _execution_summary_from_loaded(
    turn: ConversationTurn,
    *,
    run: AgentRun | None,
    skill_run: SkillRun | None,
    invocations: list[AgentInvocation],
    tool_calls: list[AgentToolCall],
    deliverables: list[Deliverable],
    attempt_counts: dict[int, int],
) -> dict | None:
    if skill_run is None and not invocations and not tool_calls:
        return None
    safe_intent = _safe_turn_intent(turn.intent)
    retry_counts = {
        tool_call.id: max(0, attempt_counts.get(tool_call.id, 0) - 1)
        for tool_call in tool_calls
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
    revision_run_id = run.result_payload.get("revision_run_id")
    revision_status = run.result_payload.get("revision_status")
    dispatch_run_id = (
        run.id
        if should_enqueue and run.status == "queued"
        else revision_run_id
        if isinstance(revision_run_id, int) and revision_status == "queued"
        else None
    )
    if dispatch_run_id is not None:
        try:
            await enqueue_agent_runtime(run_id=dispatch_run_id)
        except Exception:  # noqa: BLE001 - durable queued state is recoverable
            dispatch_deferred = True
            logger.warning(
                "Conversation run dispatch deferred",
                extra={"run_id": dispatch_run_id, "thread_id": thread.id},
                exc_info=True,
            )
    await session.refresh(turn)
    await session.refresh(run)
    projections = await _turn_projections(session, turn)
    turn_out = _turn_out(
        turn,
        projections,
        recovery_context=_recovery_context(turn, run),
    )
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
    pending = await _pending_interrupts_by_turn(session, (turn.id,))
    latest_run_by_turn = await _latest_agent_runs_by_turn(session, [turn])
    return _turn_out(
        turn,
        await _turn_projections(session, turn),
        pending.get(turn.id),
        _recovery_context(turn, latest_run_by_turn.get(turn.id)),
    )
