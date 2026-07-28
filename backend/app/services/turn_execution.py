"""Execute one main-Agent conversation Turn according to its route decision."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import require_account_access
from app.models import (
    AgentRun,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    OrchestrationPlan,
    SkillRun,
    TaskBrief,
    User,
)
from app.models.enums import BrainTaskStatus, BrainTaskType
from app.orchestrator.brain_intelligence import (
    IntelligenceUnavailable,
    brain_intelligence,
)
from app.orchestrator.brain_runtime import runtime_graph, runtime_status
from app.orchestrator.runtime_tools import build_runtime_tool_adapter
from app.schemas.conversation import (
    CreateConversationTurnRequest,
    TurnExecutionMode,
    TurnExecutionResult,
    TurnRouteDecision,
)
from app.tools import ToolExecutionContext

_TERMINAL_RUN_STATUSES = {
    "blocked",
    "cancelled",
    "completed",
    "dead_letter",
    "failed",
}
_QUERY_SKILL_CODES = {"account_data_query", "account.data_context"}
_QUERY_SKILL_CODE = "account_data_query"
_QUERY_SKILL_VERSION = 1
_QUERY_IDEMPOTENCY_KEY = "account-data-query:v1"


async def execute_conversation_turn(
    session: AsyncSession,
    user: User,
    turn: ConversationTurn,
    run: AgentRun,
    request: CreateConversationTurnRequest,
) -> TurnExecutionResult:
    """Route and execute one Turn while preserving its account ownership."""

    _require_owned_request(user, turn, run, request)
    persisted = _terminal_result(run)
    if persisted is not None:
        return persisted

    thread = await session.scalar(
        select(ConversationThread).where(
            ConversationThread.id == turn.thread_id,
            ConversationThread.org_id == user.org_id,
            ConversationThread.account_id.is_not(None),
        )
    )
    if thread is None:
        raise PermissionError("conversation Thread is unavailable")
    account = await require_account_access(session, user, thread.account_id)
    decision = await _route_turn(
        session,
        user,
        request,
        platform=account.platform.value,
    )

    if decision.mode is TurnExecutionMode.ANSWER:
        return await _deliver_task_free(
            session,
            turn=turn,
            run=run,
            account_id=account.id,
            decision=decision,
            response=_direct_answer(request.message),
        )
    if decision.mode is TurnExecutionMode.CLARIFY:
        return await _deliver_task_free(
            session,
            turn=turn,
            run=run,
            account_id=account.id,
            decision=decision,
            response=decision.clarifying_question
            or "请补充完成这次请求所需的关键信息。",
            extra_events=[
                (
                    "brain.runtime.clarification_requested",
                    "clarification",
                    {
                        "message": decision.clarifying_question
                        or "请补充完成这次请求所需的关键信息。",
                        "missing_field": decision.missing_field,
                    },
                )
            ],
        )
    if decision.mode is TurnExecutionMode.QUERY:
        return await _execute_query(
            session,
            user=user,
            thread=thread,
            turn=turn,
            run=run,
            decision=decision,
        )
    if decision.mode is TurnExecutionMode.SKILL:
        return await _block_unavailable_skill(
            session,
            thread=thread,
            turn=turn,
            run=run,
            decision=decision,
        )
    return await _execute_operation_task(
        session,
        user=user,
        thread=thread,
        turn=turn,
        run=run,
        decision=decision,
    )


def _require_owned_request(
    user: User,
    turn: ConversationTurn,
    run: AgentRun,
    request: CreateConversationTurnRequest,
) -> None:
    if (
        turn.org_id != user.org_id
        or turn.created_by_id != user.id
        or run.org_id != user.org_id
        or run.requested_by_id != user.id
        or run.thread_id != turn.thread_id
        or run.turn_id != turn.id
        or run.client_message_id != request.client_message_id
        or turn.client_message_id != request.client_message_id
        or turn.user_input != request.message
    ):
        raise PermissionError("conversation Turn execution ownership does not match")


def _terminal_result(run: AgentRun) -> TurnExecutionResult | None:
    if run.status not in _TERMINAL_RUN_STATUSES:
        return None
    payload = dict(run.result_payload or {})
    if not payload:
        return None
    return TurnExecutionResult.model_validate(payload)


async def _route_turn(
    session: AsyncSession,
    user: User,
    request: CreateConversationTurnRequest,
    *,
    platform: str,
) -> TurnRouteDecision:
    requested = (request.requested_skill_code or "").strip()
    if requested in _QUERY_SKILL_CODES:
        return TurnRouteDecision(
            mode=TurnExecutionMode.QUERY,
            intent="account_data_query",
            confidence=1,
            reason="The user explicitly selected the account data query.",
            skill_code=_QUERY_SKILL_CODE,
            requires_account_context=True,
            requires_operation_task=False,
        )
    if requested:
        return TurnRouteDecision(
            mode=TurnExecutionMode.SKILL,
            intent="explicit_skill",
            confidence=1,
            reason="The user explicitly selected a business Skill.",
            skill_code=requested,
            requires_account_context=True,
            requires_operation_task=True,
        )
    try:
        return await brain_intelligence.classify_turn(
            session,
            user.org_id,
            request.message,
            has_account=True,
            platform=platform,
        )
    except IntelligenceUnavailable:
        # A provider outage must not force ordinary conversation into an
        # OperationTask. Keep the response task-free and make no business claim.
        return TurnRouteDecision(
            mode=TurnExecutionMode.ANSWER,
            intent="conversation",
            confidence=0,
            reason="Intent service unavailable; safe task-free response.",
        )


async def _deliver_task_free(
    session: AsyncSession,
    *,
    turn: ConversationTurn,
    run: AgentRun,
    account_id: int,
    decision: TurnRouteDecision,
    response: str,
    projections: list[dict[str, Any]] | None = None,
    status: str = "completed",
    error_code: str | None = None,
    extra_events: list[tuple[str, str, dict[str, Any]]] | None = None,
) -> TurnExecutionResult:
    result = TurnExecutionResult(
        mode=decision.mode,
        status=status,
        response=response,
        task_id=None,
        projections=projections or [],
        error_code=error_code,
    )
    await runtime_graph.deliver_task_free_turn(
        session,
        turn=turn,
        run=run,
        account_id=account_id,
        route_decision=decision,
        response=response,
        result_payload=result.model_dump(mode="json"),
        status=status,
        error_code=error_code,
        extra_events=extra_events,
    )
    return result


async def _execute_query(
    session: AsyncSession,
    *,
    user: User,
    thread: ConversationThread,
    turn: ConversationTurn,
    run: AgentRun,
    decision: TurnRouteDecision,
) -> TurnExecutionResult:
    skill_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.idempotency_key == _QUERY_IDEMPOTENCY_KEY,
        )
    )
    if skill_run is not None and skill_run.status == "completed":
        data = dict(skill_run.output_snapshot or {})
    else:
        if skill_run is None:
            skill_run = SkillRun(
                org_id=user.org_id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                task_id=None,
                idempotency_key=_QUERY_IDEMPOTENCY_KEY,
                skill_code=_QUERY_SKILL_CODE,
                skill_version=_QUERY_SKILL_VERSION,
                status="running",
                input_snapshot={"account_id": thread.account_id, "days": 30},
                output_snapshot={},
            )
            session.add(skill_run)
            await session.commit()
            await session.refresh(skill_run)
        result = await build_runtime_tool_adapter().invoke(
            "account.data_context",
            {"days": 30},
            ToolExecutionContext(
                session=session,
                user=user,
                project_id=thread.project_id,
                account_id=thread.account_id,
                task_id=None,
                invocation_id=None,
            ),
        )
        data = dict(result)
        skill_run.status = "completed"
        skill_run.output_snapshot = data
        skill_run.error_code = None
        await session.commit()

    projection = {
        "type": "account_data",
        "account_id": thread.account_id,
        "skill_code": _QUERY_SKILL_CODE,
        "skill_run_id": skill_run.id,
        "data": data,
    }
    return await _deliver_task_free(
        session,
        turn=turn,
        run=run,
        account_id=thread.account_id,
        decision=decision,
        response="已读取当前账号的数据概览，可继续告诉我你想分析的指标。",
        projections=[projection],
        extra_events=[
            (
                "brain.runtime.tool_completed",
                "account-data-context",
                {
                    "message": "Account data context loaded.",
                    "tool_code": "account.data_context",
                    "skill_run_id": skill_run.id,
                    "result": data,
                },
            )
        ],
    )


async def _block_unavailable_skill(
    session: AsyncSession,
    *,
    thread: ConversationThread,
    turn: ConversationTurn,
    run: AgentRun,
    decision: TurnRouteDecision,
) -> TurnExecutionResult:
    code = decision.skill_code or "unknown"
    skill_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.idempotency_key == f"skill:{code}:v1",
        )
    )
    if skill_run is None:
        skill_run = SkillRun(
            org_id=turn.org_id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            task_id=None,
            idempotency_key=f"skill:{code}:v1",
            skill_code=code,
            skill_version=1,
            status="blocked",
            input_snapshot={"account_id": thread.account_id},
            output_snapshot={
                "code": "SKILL_EXECUTOR_UNAVAILABLE",
                "message": "该能力尚未接入执行器。",
            },
            error_code="SKILL_EXECUTOR_UNAVAILABLE",
        )
        session.add(skill_run)
        await session.commit()
    return await _deliver_task_free(
        session,
        turn=turn,
        run=run,
        account_id=thread.account_id,
        decision=decision,
        response="该能力尚未接入执行器，暂时无法执行。请稍后重试或改为查询账号数据。",
        status="blocked",
        error_code="SKILL_EXECUTOR_UNAVAILABLE",
        projections=[
            {
                "type": "execution_blocked",
                "skill_code": code,
                "skill_run_id": skill_run.id,
                "code": "SKILL_EXECUTOR_UNAVAILABLE",
                "recovery_action": "稍后重试或改为查询账号数据。",
            }
        ],
    )


async def _execute_operation_task(
    session: AsyncSession,
    *,
    user: User,
    thread: ConversationThread,
    turn: ConversationTurn,
    run: AgentRun,
    decision: TurnRouteDecision,
) -> TurnExecutionResult:
    task = await session.get(BrainTask, run.task_id) if run.task_id else None
    if task is None:
        task = BrainTask(
            org_id=user.org_id,
            created_by_id=user.id,
            title=turn.user_input[:300],
            type=BrainTaskType.REVIEW_OPTIMIZATION,
            status=BrainTaskStatus.RUNNING,
            progress=0,
            current_focus="Main Agent is preparing this operation task.",
            risk_count=1 if decision.mode is TurnExecutionMode.ACTION else 0,
            runtime_mode="langgraph",
        )
        task.brief = TaskBrief(
            goal=turn.user_input,
            project_id=thread.project_id,
            project_name=None,
            account_group_id=None,
            account_group_name=None,
            platforms=["douyin"],
            account_ids=[thread.account_id],
            cycle="current_turn",
            budget=None,
            content_goal=turn.user_input,
            risk_constraints=(
                ["External actions require explicit approval."]
                if decision.mode is TurnExecutionMode.ACTION
                else []
            ),
            expected_outputs=["operation_result"],
            confirmation_actions=(
                ["Confirm external actions."]
                if decision.mode is TurnExecutionMode.ACTION
                else []
            ),
        )
        task.plan = OrchestrationPlan(
            summary="Execute the routed operation task.",
            steps=[],
            quality_gates=[],
            estimated_cost=Decimal("0"),
            requires_human_confirmation=decision.mode is TurnExecutionMode.ACTION,
        )
        session.add(task)
        await session.flush()
        run.task_id = task.id
        run.phase = "runtime"
        await session.commit()

    await runtime_graph.start_routed(
        session,
        task,
        route_decision=decision,
        client_message_id=run.client_message_id,
        agent_run_id=run.id,
        agent_run_attempt=run.attempt,
    )
    task_state = await runtime_status(session, task)
    response = task.current_focus or "本轮运营任务已完成。"
    result = TurnExecutionResult(
        mode=decision.mode,
        status="completed",
        response=response,
        task_id=task.id,
        projections=[],
    )
    turn.intent = decision.model_dump(mode="json")
    turn.assistant_response = response
    run.status = "completed"
    run.phase = "complete"
    run.finished_at = datetime.now(UTC)
    run.result_payload = {
        **result.model_dump(mode="json"),
        "task_status": task_state,
    }
    await session.commit()
    return result


def _direct_answer(message: str) -> str:
    normalized = message.strip().lower()
    if normalized in {"谢谢", "感謝", "thanks", "thank you"}:
        return "不客气。你可以继续告诉我账号运营中想解决的问题。"
    return "你好，我在。你可以直接告诉我账号运营目标、数据问题或想推进的工作。"
