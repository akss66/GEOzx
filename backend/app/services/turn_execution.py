"""Execute one main-Agent conversation Turn according to its route decision."""

from __future__ import annotations

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
from app.orchestrator.skill_runtime import skill_runtime
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
    "stopped",
    "waiting_decision",
    "waiting_permission",
    "waiting_user",
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
    try:
        decision = await _route_turn(
            session,
            user,
            request,
            platform=account.platform.value,
        )
    except IntelligenceUnavailable:
        unavailable = TurnRouteDecision(
            mode=TurnExecutionMode.ANSWER,
            intent="intelligence_unavailable",
            confidence=0,
            reason="Intent intelligence is temporarily unavailable.",
        )
        return await _deliver_task_free(
            session,
            turn=turn,
            run=run,
            account_id=account.id,
            decision=unavailable,
            response="我暂时无法可靠判断这条请求应如何执行。请稍后重试，或明确说明只查询数据还是创建正式任务。",
            status="blocked",
            error_code="INTELLIGENCE_UNAVAILABLE",
        )

    if request.execution_preference == "DISCUSS_ONLY" and decision.mode in {
        TurnExecutionMode.SKILL,
        TurnExecutionMode.TASK,
        TurnExecutionMode.ACTION,
    }:
        return await _deliver_task_free(
            session,
            turn=turn,
            run=run,
            account_id=account.id,
            decision=decision,
            response="已按“仅讨论”处理：本轮未执行能力、未创建正式任务，也未触发外部动作。你确认后我再继续。",
        )
    if request.execution_preference == "FORMAL_TASK" and decision.mode not in {
        TurnExecutionMode.CLARIFY,
        TurnExecutionMode.SKILL,
    }:
        decision = TurnRouteDecision(
            mode=TurnExecutionMode.TASK,
            intent=decision.intent,
            confidence=decision.confidence,
            reason=f"Formal task requested. {decision.reason}",
            requires_account_context=True,
            requires_operation_task=True,
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
        if decision.skill_code == "account_inspection":
            return await _execute_composite_skill(
                session,
                user=user,
                thread=thread,
                turn=turn,
                run=run,
                decision=decision,
            )
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


async def _execute_composite_skill(
    session: AsyncSession,
    *,
    user: User,
    thread: ConversationThread,
    turn: ConversationTurn,
    run: AgentRun,
    decision: TurnRouteDecision,
) -> TurnExecutionResult:
    executed = await skill_runtime.execute(
        session,
        user=user,
        thread=thread,
        turn=turn,
        run=run,
        skill_code=decision.skill_code or "",
        days=30,
    )
    projections: list[dict[str, Any]] = []
    if executed.artifact_id is not None:
        projections.append(
            {
                "type": "artifact",
                "artifact_id": executed.artifact_id,
                "artifact_type": executed.artifact_type,
                "skill_run_id": executed.skill_run_id,
                "account_id": thread.account_id,
                "report": executed.report,
            }
        )
    elif executed.error_code is not None:
        projections.append(
            {
                "type": "execution_blocked",
                "artifact_type": executed.artifact_type,
                "skill_run_id": executed.skill_run_id,
                "account_id": thread.account_id,
                "code": executed.error_code,
            }
        )
    result = TurnExecutionResult(
        mode=TurnExecutionMode.SKILL,
        status=executed.status,
        response=executed.response,
        task_id=executed.task_id,
        projections=projections,
        error_code=executed.error_code,
    )
    task = (
        await session.get(BrainTask, executed.task_id)
        if executed.task_id is not None
        else None
    )
    if task is None:
        raise RuntimeError("composite Skill did not persist its compatibility task")
    task_status = (
        BrainTaskStatus.COMPLETED
        if executed.status == "completed"
        else (
            BrainTaskStatus.FAILED
            if executed.status in {"blocked", "failed", "stopped"}
            else BrainTaskStatus.RUNNING
        )
    )
    await runtime_graph.deliver_operation_turn_state(
        session,
        task=task,
        turn=turn,
        run=run,
        account_id=thread.account_id,
        project_id=thread.project_id,
        response=executed.response,
        result_payload=result.model_dump(mode="json"),
        run_status=executed.status,
        task_status=task_status,
        error_code=executed.error_code,
    )
    return result


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
    return await brain_intelligence.classify_turn(
        session,
        user.org_id,
        request.message,
        has_account=True,
        platform=platform,
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
        try:
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
        except Exception:  # noqa: BLE001 - provider details must not escape
            return await _close_query_failure(
                session,
                account_id=thread.account_id,
                turn=turn,
                run=run,
                skill_run=skill_run,
                decision=decision,
                error_code="QUERY_TOOL_UNAVAILABLE",
                response="账号数据暂时无法读取。请检查账号授权和数据同步状态后重试。",
            )
        data = dict(result)
        result_account_id = data.get("account_id")
        if (
            isinstance(result_account_id, bool)
            or not isinstance(result_account_id, int)
            or result_account_id != thread.account_id
        ):
            return await _close_query_failure(
                session,
                account_id=thread.account_id,
                turn=turn,
                run=run,
                skill_run=skill_run,
                decision=decision,
                error_code="TOOL_RESULT_SCOPE_MISMATCH",
                response="账号数据返回范围与当前对话账号不一致，本轮已停止。请刷新账号后重试。",
            )
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


async def _close_query_failure(
    session: AsyncSession,
    *,
    account_id: int,
    turn: ConversationTurn,
    run: AgentRun,
    skill_run: SkillRun,
    decision: TurnRouteDecision,
    error_code: str,
    response: str,
) -> TurnExecutionResult:
    turn_id = turn.id
    run_id = run.id
    skill_run_id = skill_run.id
    if session.sync_session.is_active:
        persisted_turn = turn
        persisted_run = run
        persisted_skill_run = skill_run
    else:
        await session.rollback()
        persisted_turn = await session.get(ConversationTurn, turn_id)
        persisted_run = await session.get(AgentRun, run_id)
        persisted_skill_run = await session.get(SkillRun, skill_run_id)
    if persisted_turn is None or persisted_run is None or persisted_skill_run is None:
        raise RuntimeError("query execution ownership disappeared")
    persisted_skill_run.status = "failed"
    persisted_skill_run.output_snapshot = {
        "code": error_code,
        "message": response,
    }
    persisted_skill_run.error_code = error_code
    return await _deliver_task_free(
        session,
        turn=persisted_turn,
        run=persisted_run,
        account_id=account_id,
        decision=decision,
        response=response,
        status="failed",
        error_code=error_code,
        extra_events=[
            (
                "brain.runtime.tool_failed",
                "account-data-context",
                {
                    "message": response,
                    "tool_code": "account.data_context",
                    "skill_run_id": persisted_skill_run.id,
                    "error_code": error_code,
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

    task_id = task.id
    turn_id = turn.id
    run_id = run.id
    account_id = thread.account_id
    project_id = thread.project_id
    try:
        await runtime_graph.start_routed(
            session,
            task,
            route_decision=decision,
            client_message_id=run.client_message_id,
            agent_run_id=run.id,
            agent_run_attempt=run.attempt,
        )
        task_state = await runtime_status(session, task)
    except Exception as exc:  # noqa: BLE001 - persist only a safe operation failure
        await session.rollback()
        task = await session.get(BrainTask, task_id)
        turn = await session.get(ConversationTurn, turn_id)
        run = await session.get(AgentRun, run_id)
        if task is None or turn is None or run is None:
            raise RuntimeError("operation execution ownership disappeared") from exc
        task_state = "failed"

    return await _close_operation_state(
        session,
        account_id=account_id,
        project_id=project_id,
        turn=turn,
        run=run,
        task=task,
        decision=decision,
        runtime_state=task_state,
    )


async def _close_operation_state(
    session: AsyncSession,
    *,
    account_id: int,
    project_id: int | None,
    turn: ConversationTurn,
    run: AgentRun,
    task: BrainTask,
    decision: TurnRouteDecision,
    runtime_state: str,
) -> TurnExecutionResult:
    states: dict[str, tuple[str, BrainTaskStatus, str | None, str]] = {
        "completed": (
            "completed",
            BrainTaskStatus.COMPLETED,
            None,
            task.current_focus or "本轮运营任务已完成。",
        ),
        "waiting_permission": (
            "waiting_permission",
            BrainTaskStatus.PENDING_CONFIRMATION,
            None,
            "任务已暂停，正在等待你确认受控工具或外部动作。",
        ),
        "waiting_decision": (
            "waiting_decision",
            BrainTaskStatus.PENDING_CONFIRMATION,
            None,
            "任务已暂停，正在等待你选择下一步方案。",
        ),
        "waiting_user": (
            "waiting_user",
            BrainTaskStatus.PENDING_CONFIRMATION,
            None,
            "任务已暂停，正在等待你补充必要信息。",
        ),
        "failed": (
            "failed",
            BrainTaskStatus.FAILED,
            "OPERATION_RUNTIME_FAILED",
            "正式任务未能完成。请检查账号授权、数据范围和专家配置后重试。",
        ),
        "stopped": (
            "stopped",
            BrainTaskStatus.PENDING_CONFIRMATION,
            "OPERATION_STOPPED",
            "本轮生成已停止。你可以调整要求后重新发起。",
        ),
    }
    run_status, task_status, error_code, response = states.get(
        runtime_state,
        (
            "failed",
            BrainTaskStatus.FAILED,
            "OPERATION_RUNTIME_INCOMPLETE",
            "正式任务没有形成可确认的结束状态，本轮已安全停止。请重试。",
        ),
    )
    result = TurnExecutionResult(
        mode=decision.mode,
        status=run_status,
        response=response,
        task_id=task.id,
        projections=[],
        error_code=error_code,
    )
    turn.intent = decision.model_dump(mode="json")
    await runtime_graph.deliver_operation_turn_state(
        session,
        task=task,
        turn=turn,
        run=run,
        account_id=account_id,
        project_id=project_id,
        response=response,
        result_payload={
            **result.model_dump(mode="json"),
            "task_status": runtime_state,
        },
        run_status=run_status,
        task_status=task_status,
        error_code=error_code,
    )
    return result


def _direct_answer(message: str) -> str:
    normalized = message.strip().lower()
    if normalized in {"谢谢", "感謝", "thanks", "thank you"}:
        return "不客气。你可以继续告诉我账号运营中想解决的问题。"
    return "你好，我在。你可以直接告诉我账号运营目标、数据问题或想推进的工作。"
