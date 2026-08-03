"""Execute one main-Agent conversation Turn according to its route decision."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_failures import FailureDisposition, classify_runtime_failure
from app.core.workspace_access import require_account_access
from app.models import (
    AgentRun,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    OrchestrationPlan,
    SkillRun,
    TaskBrief,
    User,
)
from app.models.enums import (
    BrainTaskStatus,
    BrainTaskType,
    ContentStage,
    ContentStatus,
)
from app.orchestrator.brain_intelligence import (
    IntelligenceUnavailable,
    brain_intelligence,
)
from app.orchestrator.brain_runtime import runtime_graph, runtime_status
from app.orchestrator.capability_router import (
    SkillUnavailable,
    route_deterministic_request,
    route_explicit_request,
)
from app.orchestrator.runtime_tools import build_runtime_tool_adapter
from app.orchestrator.skill_runtime import skill_input_hash, skill_runtime
from app.orchestrator.skills.public_catalog import PUBLIC_SKILL_POLICIES
from app.orchestrator.skills.registry import skill_registry
from app.schemas.capability_request import CapabilityRequest
from app.schemas.conversation import (
    CreateConversationTurnRequest,
    TurnExecutionMode,
    TurnExecutionResult,
    TurnRouteDecision,
)
from app.services.capability_request import build_capability_request
from app.services.runtime_state import (
    RuntimeEventSpec,
    RuntimeStateScope,
    close_runtime_state,
)
from app.services.turn_observability import (
    TurnObservabilityScope,
    bind_turn_observability,
    mark_execution_started,
    record_route_completed,
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
_MODEL_SKILL_ALIASES = {
    "account_audit": "account_inspection",
    "account_diagnosis": "account_inspection",
    "account_health_check": "account_inspection",
}
log = logging.getLogger("dyflow.main_agent_v2")


async def execute_conversation_turn(
    session: AsyncSession,
    user: User,
    turn: ConversationTurn,
    run: AgentRun,
    request: CreateConversationTurnRequest,
    *,
    execution_owner: str | None = None,
    resume_skill_run: SkillRun | None = None,
) -> TurnExecutionResult:
    """Route and execute one Turn while preserving its account ownership."""

    _require_owned_request(user, turn, run, request)
    if resume_skill_run is not None:
        _require_resumable_skill_run(
            user=user,
            turn=turn,
            run=run,
            request=request,
            skill_run=resume_skill_run,
        )
    persisted = _terminal_result(run)
    if persisted is not None:
        return persisted
    with bind_turn_observability(
        TurnObservabilityScope(
            org_id=turn.org_id,
            thread_id=turn.thread_id,
            turn_id=turn.id,
            run_id=run.id,
            turn_created_at=turn.created_at,
        )
    ):
        mark_execution_started()
        return await _execute_conversation_turn(
            session,
            user,
            turn,
            run,
            request,
            execution_owner=execution_owner,
            resume_skill_run=resume_skill_run,
        )


async def _execute_conversation_turn(
    session: AsyncSession,
    user: User,
    turn: ConversationTurn,
    run: AgentRun,
    request: CreateConversationTurnRequest,
    *,
    execution_owner: str | None = None,
    resume_skill_run: SkillRun | None = None,
) -> TurnExecutionResult:
    """Bound implementation for one non-terminal Turn."""

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
    capability_request = build_capability_request(
        user=user,
        thread=thread,
        turn=turn,
        run=run,
        request_payload=request.model_dump(mode="python"),
    )
    run.request_payload = {
        **dict(run.request_payload or {}),
        "structured_input": capability_request.structured_input,
        "constraints": capability_request.constraints,
        "attachment_ids": capability_request.attachment_ids,
    }
    try:
        decision = (
            _route_persisted_skill_run(resume_skill_run)
            if resume_skill_run is not None
            else await _route_turn(
                session,
                user,
                request,
                platform=account.platform.value,
            )
        )
    except SkillUnavailable as skill_unavailable:
        await record_route_completed(session)
        return await _block_invalid_explicit_skill(
            session,
            thread=thread,
            turn=turn,
            run=run,
            requested_skill_code=(request.requested_skill_code or "").strip(),
            unavailable=skill_unavailable,
        )
    except IntelligenceUnavailable:
        await record_route_completed(session)
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
    await record_route_completed(session)

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
        realtime_stream = runtime_graph.task_free_realtime_stream(
            turn=turn,
            run=run,
        )
        try:
            answer = await brain_intelligence.answer_turn(
                session,
                user.org_id,
                request.message,
                operating_context=_conversation_operating_context(account),
                history=await _conversation_history(
                    session,
                    thread_id=thread.id,
                    before_turn_id=turn.id,
                ),
                scope={
                    "account_id": account.id,
                    "thread_id": thread.id,
                    "turn_id": turn.id,
                },
                stream_observer=realtime_stream.observe,
            )
        except IntelligenceUnavailable:
            return await _deliver_task_free(
                session,
                turn=turn,
                run=run,
                account_id=account.id,
                decision=decision,
                response="运营大脑暂时无法生成这条回复，请稍后重试。",
                status="blocked",
                error_code="ANSWER_MODEL_UNAVAILABLE",
                stream_seq_start=realtime_stream.next_sequence,
            )
        return await _deliver_task_free(
            session,
            turn=turn,
            run=run,
            account_id=account.id,
            decision=decision,
            response=answer,
            response_streamed=realtime_stream.has_deltas,
            stream_seq_start=realtime_stream.next_sequence,
        )
    if decision.mode is TurnExecutionMode.CLARIFY:
        return await _deliver_task_free(
            session,
            turn=turn,
            run=run,
            account_id=account.id,
            decision=decision,
            response=decision.clarifying_question or "请补充完成这次请求所需的关键信息。",
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
        return await _execute_composite_skill(
            session,
            user=user,
            thread=thread,
            turn=turn,
            run=run,
            decision=decision,
            capability_request=capability_request,
            execution_owner=execution_owner,
            resume_skill_run=resume_skill_run,
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
    capability_request: CapabilityRequest,
    execution_owner: str | None = None,
    resume_skill_run: SkillRun | None = None,
) -> TurnExecutionResult:
    del capability_request  # Task 2 wires this validated request into the Skill input model.
    account_id = thread.account_id
    project_id = thread.project_id
    execution_kwargs: dict[str, Any] = {
        "user": user,
        "thread": thread,
        "turn": turn,
        "run": run,
        "skill_code": decision.skill_code or "",
        "days": 30,
    }
    if execution_owner is not None:
        execution_kwargs["lease_owner"] = execution_owner
    if resume_skill_run is not None:
        execution_kwargs["resume_skill_run"] = resume_skill_run
    executed = await skill_runtime.execute(session, **execution_kwargs)
    projections: list[dict[str, Any]] = []
    if executed.artifact_id is not None:
        projections.append(
            {
                "type": "artifact",
                "artifact_id": executed.artifact_id,
                "artifact_type": executed.artifact_type,
                "skill_run_id": executed.skill_run_id,
                "account_id": account_id,
                "report": executed.report,
            }
        )
    elif executed.error_code is not None:
        projections.append(
            {
                "type": "execution_blocked",
                "artifact_type": executed.artifact_type,
                "skill_run_id": executed.skill_run_id,
                "account_id": account_id,
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
    if executed.status == "running":
        return result
    task = await session.get(BrainTask, executed.task_id) if executed.task_id is not None else None
    if task is None:
        raise RuntimeError("composite Skill did not persist its compatibility task")
    persisted_skill_run = await session.get(SkillRun, executed.skill_run_id)
    # The Skill runtime commits multiple durable ledgers. Callers and tests may
    # expire the request-scoped ORM objects across those commits, so explicitly
    # refresh instead of allowing scalar attribute access to trigger async IO.
    await session.refresh(user)
    await session.refresh(turn)
    await session.refresh(run)
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            turn_id=turn.id,
            skill_run_id=(persisted_skill_run.id if persisted_skill_run is not None else None),
            task_id=task.id,
            account_id=account_id,
            project_id=project_id,
            content_item_id=task.content_item_id,
            result_payload=result.model_dump(mode="json"),
            intent=decision.model_dump(mode="json"),
        ),
        status=executed.status,
        message=executed.response,
        error_code=executed.error_code,
    )
    _log_turn_completion(turn, run, result)
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


def _require_resumable_skill_run(
    *,
    user: User,
    turn: ConversationTurn,
    run: AgentRun,
    request: CreateConversationTurnRequest,
    skill_run: SkillRun,
) -> None:
    requested_skill_code = (request.requested_skill_code or "").strip()
    skill_code_matches = (
        not requested_skill_code
        or skill_run.skill_code == requested_skill_code
        or (
            skill_run.skill_code in _QUERY_SKILL_CODES
            and requested_skill_code in _QUERY_SKILL_CODES
        )
    )
    if (
        skill_run.org_id != user.org_id
        or skill_run.run_id != run.id
        or skill_run.thread_id != turn.thread_id
        or skill_run.turn_id != turn.id
        or not skill_code_matches
    ):
        raise PermissionError("persisted SkillRun recovery ownership does not match")


def _terminal_result(run: AgentRun) -> TurnExecutionResult | None:
    if run.status not in _TERMINAL_RUN_STATUSES:
        return None
    payload = dict(run.result_payload or {})
    if not payload:
        return None
    return TurnExecutionResult.model_validate(payload)


def _route_persisted_skill_run(skill_run: SkillRun) -> TurnRouteDecision:
    if skill_run.skill_code in _QUERY_SKILL_CODES:
        return TurnRouteDecision(
            mode=TurnExecutionMode.QUERY,
            intent="account_data_query",
            confidence=1,
            reason="Resume the persisted account data query.",
            skill_code=_QUERY_SKILL_CODE,
            requires_account_context=True,
            requires_operation_task=False,
        )
    return TurnRouteDecision(
        mode=TurnExecutionMode.SKILL,
        intent="explicit_skill",
        confidence=1,
        reason="Resume the persisted SkillRun.",
        skill_code=skill_run.skill_code,
        requires_account_context=True,
        requires_operation_task=True,
    )


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
        decision = route_explicit_request(
            requested,
            platform=platform,
            registry=skill_registry,
            has_account=True,
        )
        policy = PUBLIC_SKILL_POLICIES.get(requested)
        if policy is None or "composer" not in policy.surfaces:
            raise SkillUnavailable(
                code="unpublished_skill",
                reason="requested_skill_not_published",
            )
        if not policy.enabled or user.role not in policy.allowed_roles:
            raise SkillUnavailable(
                code="skill_unavailable",
                reason="requested_skill_not_available",
            )
        if decision is None:
            raise SkillUnavailable(
                code="unknown_skill",
                reason="requested_skill_not_registered",
            )
        return decision
    deterministic_decision = route_deterministic_request(
        request.message,
        platform=platform,
        registry=skill_registry,
        has_account=True,
    )
    if deterministic_decision is not None:
        return deterministic_decision
    decision = await brain_intelligence.classify_turn(
        session,
        user.org_id,
        request.message,
        has_account=True,
        platform=platform,
        registry=skill_registry,
    )
    return _normalize_model_skill_route(
        decision,
        user=user,
        platform=platform,
    )


def _normalize_model_skill_route(
    decision: TurnRouteDecision,
    *,
    user: User,
    platform: str,
) -> TurnRouteDecision:
    """Accept only published Skill codes and normalize known model aliases."""

    if decision.mode is not TurnExecutionMode.SKILL:
        return decision
    raw_code = (decision.skill_code or "").strip()
    code = _MODEL_SKILL_ALIASES.get(raw_code, raw_code)
    try:
        definition = skill_registry.get(code)
    except KeyError as exc:
        raise IntelligenceUnavailable("model selected an unknown Skill") from exc
    policy = PUBLIC_SKILL_POLICIES.get(code)
    if (
        platform not in definition.supported_platforms
        or policy is None
        or "composer" not in policy.surfaces
        or not policy.enabled
        or user.role not in policy.allowed_roles
    ):
        raise IntelligenceUnavailable("model selected an unavailable Skill")
    return decision.model_copy(update={"skill_code": code})


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
    response_streamed: bool = False,
    stream_seq_start: int = 0,
    extra_events: list[tuple[str, str, dict[str, Any]]] | None = None,
    skill_run_id: int | None = None,
    skill_output_snapshot: dict[str, Any] | None = None,
) -> TurnExecutionResult:
    result = TurnExecutionResult(
        mode=decision.mode,
        status=status,
        response=response,
        task_id=None,
        projections=projections or [],
        error_code=error_code,
    )
    runtime_events = [
        RuntimeEventSpec(
            event_type="brain.runtime.started",
            semantic_key="turn-started",
            payload={"message": "Main Agent received this conversation Turn."},
        ),
        RuntimeEventSpec(
            event_type="brain.runtime.intent_classified",
            semantic_key="turn-route",
            payload={
                "message": "Main Agent selected the execution route.",
                "route_decision": decision.model_dump(mode="json"),
            },
        ),
        *(
            RuntimeEventSpec(
                event_type=event_type,
                semantic_key=semantic_key,
                payload=payload,
            )
            for event_type, semantic_key, payload in (extra_events or [])
        ),
    ]
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            turn_id=turn.id,
            skill_run_id=skill_run_id,
            account_id=account_id,
            result_payload=result.model_dump(mode="json"),
            skill_output_snapshot=skill_output_snapshot,
            intent=decision.model_dump(mode="json"),
            response_streamed=response_streamed,
            stream_seq_start=stream_seq_start,
            extra_events=tuple(runtime_events),
        ),
        status=status,
        message=response,
        error_code=error_code,
    )
    _log_turn_completion(turn, run, result)
    return result


def _log_turn_completion(
    turn: ConversationTurn,
    run: AgentRun,
    result: TurnExecutionResult,
) -> None:
    """Emit allowlisted rollout diagnostics for one finalized Turn."""

    payload = {
        "event": "main_agent_turn_completed",
        "thread_id": turn.thread_id,
        "turn_id": turn.id,
        "run_id": run.id,
        "mode": result.mode.value,
        "skill_run_id": _first_projection_id(result.projections, "skill_run_id"),
        "task_id": result.task_id,
        "artifact_ids": _projection_ids(result.projections, "artifact_id"),
        "status": result.status,
    }
    log.info(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        extra=payload,
    )


def _first_projection_id(
    projections: list[dict[str, Any]],
    field: str,
) -> int | None:
    for projection in projections:
        value = projection.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _projection_ids(
    projections: list[dict[str, Any]],
    field: str,
) -> list[int]:
    ids: list[int] = []
    for projection in projections:
        value = projection.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            ids.append(value)
    return ids


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
            query_input = {"account_id": thread.account_id, "days": 30}
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
                input_snapshot=query_input,
                input_hash=skill_input_hash(query_input),
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
        except Exception as exc:  # noqa: BLE001 - classify before safe terminal
            if classify_runtime_failure(exc) is FailureDisposition.RETRYABLE:
                raise
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
        response=_format_account_data_summary(data),
        projections=[projection],
        skill_run_id=skill_run.id,
        skill_output_snapshot=data,
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
    persisted_turn: ConversationTurn | None
    persisted_run: AgentRun | None
    persisted_skill_run: SkillRun | None
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
    skill_output_snapshot = {
        "code": error_code,
        "message": response,
    }
    return await _deliver_task_free(
        session,
        turn=persisted_turn,
        run=persisted_run,
        account_id=account_id,
        decision=decision,
        response=response,
        status="failed",
        error_code=error_code,
        skill_run_id=persisted_skill_run.id,
        skill_output_snapshot=skill_output_snapshot,
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


async def _block_invalid_explicit_skill(
    session: AsyncSession,
    *,
    thread: ConversationThread,
    turn: ConversationTurn,
    run: AgentRun,
    requested_skill_code: str,
    unavailable: SkillUnavailable,
) -> TurnExecutionResult:
    error_code = unavailable.code.upper()
    decision = TurnRouteDecision(
        mode=TurnExecutionMode.SKILL,
        intent="explicit_skill",
        confidence=1,
        reason=unavailable.reason,
        skill_code=requested_skill_code or None,
        requires_account_context=True,
        requires_operation_task=True,
    )
    return await _deliver_task_free(
        session,
        turn=turn,
        run=run,
        account_id=thread.account_id,
        decision=decision,
        response="该能力当前不可用，请从当前公开能力目录重新选择。",
        status="blocked",
        error_code=error_code,
        projections=[
            {
                "type": "execution_blocked",
                "skill_code": requested_skill_code,
                "code": error_code,
                "recovery_action": "请从当前公开能力目录重新选择。",
            }
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
        unavailable_input = {"account_id": thread.account_id}
        skill_run = SkillRun(
            org_id=turn.org_id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            task_id=None,
            idempotency_key=f"skill:{code}:v1",
            skill_code=code,
            skill_version=1,
            status="running",
            input_snapshot=unavailable_input,
            input_hash=skill_input_hash(unavailable_input),
            output_snapshot={
                "code": "SKILL_EXECUTOR_UNAVAILABLE",
                "message": "该能力尚未接入执行器。",
            },
            error_code="SKILL_EXECUTOR_UNAVAILABLE",
        )
        session.add(skill_run)
        await session.flush()
    return await _deliver_task_free(
        session,
        turn=turn,
        run=run,
        account_id=thread.account_id,
        decision=decision,
        response="该能力尚未接入执行器，暂时无法执行。请稍后重试或改为查询账号数据。",
        status="blocked",
        error_code="SKILL_EXECUTOR_UNAVAILABLE",
        skill_run_id=skill_run.id,
        skill_output_snapshot={
            "code": "SKILL_EXECUTOR_UNAVAILABLE",
            "message": "该能力尚未接入执行器。",
        },
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
        content = ContentItem(
            project_id=thread.project_id,
            created_by_id=user.id,
            account_id=thread.account_id,
            title=turn.user_input[:300],
            current_stage=ContentStage.OPERATION,
            status=ContentStatus.IN_PROGRESS,
        )
        session.add(content)
        await session.flush()
        task = BrainTask(
            org_id=user.org_id,
            created_by_id=user.id,
            content_item_id=content.id,
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
                ["Confirm external actions."] if decision.mode is TurnExecutionMode.ACTION else []
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
        if classify_runtime_failure(exc) is FailureDisposition.RETRYABLE:
            raise
        task = await session.get(BrainTask, task_id)
        persisted_turn = await session.get(ConversationTurn, turn_id)
        persisted_run = await session.get(AgentRun, run_id)
        recovered_turn = persisted_turn
        recovered_run = persisted_run
        if task is None or recovered_turn is None or recovered_run is None:
            raise RuntimeError("operation execution ownership disappeared") from exc
        turn = recovered_turn
        run = recovered_run
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
    states: dict[str, tuple[str, str | None, str]] = {
        "completed": (
            "completed",
            None,
            task.current_focus or "本轮运营任务已完成。",
        ),
        "waiting_permission": (
            "waiting_permission",
            None,
            "任务已暂停，正在等待你确认受控工具或外部动作。",
        ),
        "waiting_decision": (
            "waiting_decision",
            None,
            "任务已暂停，正在等待你选择下一步方案。",
        ),
        "waiting_user": (
            "waiting_user",
            None,
            "任务已暂停，正在等待你补充必要信息。",
        ),
        "failed": (
            "failed",
            "OPERATION_RUNTIME_FAILED",
            "正式任务未能完成。请检查账号授权、数据范围和专家配置后重试。",
        ),
        "stopped": (
            "stopped",
            "OPERATION_STOPPED",
            "本轮生成已停止。你可以调整要求后重新发起。",
        ),
    }
    run_status, error_code, response = states.get(
        runtime_state,
        (
            "failed",
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
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            turn_id=turn.id,
            task_id=task.id,
            account_id=account_id,
            project_id=project_id,
            content_item_id=task.content_item_id,
            result_payload={
                **result.model_dump(mode="json"),
                "task_status": runtime_state,
            },
            intent=decision.model_dump(mode="json"),
        ),
        status=run_status,
        message=response,
        error_code=error_code,
    )
    _log_turn_completion(turn, run, result)
    return result


async def _conversation_history(
    session: AsyncSession,
    *,
    thread_id: int,
    before_turn_id: int,
) -> list[dict[str, str]]:
    rows = list(
        await session.scalars(
            select(ConversationTurn)
            .where(
                ConversationTurn.thread_id == thread_id,
                ConversationTurn.id < before_turn_id,
            )
            .order_by(ConversationTurn.id.desc())
            .limit(6)
        )
    )
    messages: list[dict[str, str]] = []
    for row in reversed(rows):
        messages.append({"role": "user", "content": row.user_input})
        if row.assistant_response:
            messages.append({"role": "assistant", "content": row.assistant_response})
    return messages


def _conversation_operating_context(account: Any) -> str:
    platform = {
        "douyin": "抖音",
        "xiaohongshu": "小红书",
        "wechat_channels": "视频号",
    }.get(account.platform.value, account.platform.value)
    return (
        f"当前平台：{platform}；当前账号：{account.nickname}（账号 ID {account.id}）；"
        "当前项目：未选择项目。"
    )


def _format_account_data_summary(data: dict[str, Any]) -> str:
    data_status = data.get("data_status")
    pending_imports = data.get("pending_imports")
    pending_imports = pending_imports if isinstance(pending_imports, list) else []
    if data_status == "pending_import":
        lines = ["当前账号暂无已正式写入的可分析数据。"]
        first = next((item for item in pending_imports if isinstance(item, dict)), None)
        if first is not None:
            batch_id = first.get("batch_id")
            row_count = first.get("row_count")
            period_start = first.get("period_start")
            period_end = first.get("period_end")
            detail_parts: list[str] = []
            if isinstance(row_count, int) and not isinstance(row_count, bool):
                detail_parts.append(f"{row_count} 行")
            if isinstance(period_start, str) and isinstance(period_end, str):
                detail_parts.append(f"数据范围 {period_start} 至 {period_end}")
            details = f"：{'，'.join(detail_parts)}" if detail_parts else ""
            lines.append(f"发现待确认导入批次 #{batch_id}{details}。")
        lines.append("请先在数据中心完成校验并正式写入，写入后我才能读取和分析这些指标。")
        return "\n".join(lines)
    if data_status == "empty":
        return "当前账号暂无可分析数据。请先在数据中心同步或导入账号数据。"

    period = data.get("data_period")
    uses_observed_period = isinstance(period, dict)
    if not uses_observed_period:
        period = data.get("period")
    period = period if isinstance(period, dict) else {}
    days = period.get("days")
    start = period.get("start")
    end = period.get("end")
    if isinstance(start, str) and isinstance(end, str):
        query_window = data.get("query_window")
        query_window = query_window if isinstance(query_window, dict) else {}
        query_days = query_window.get("days", days)
        if isinstance(query_days, int) and query_days > 0:
            day_suffix = (
                f"（查询近 {query_days} 天）"
                if uses_observed_period
                else f"（近 {query_days} 天）"
            )
        else:
            day_suffix = ""
        period_text = f"{start} 至 {end}{day_suffix}"
    else:
        period_text = f"近 {days} 天" if isinstance(days, int) and days > 0 else "当前周期"
    metrics = data.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    labels = {
        "exposure": "曝光量",
        "impressions": "曝光量",
        "play": "播放量",
        "play_count": "播放量",
        "views": "播放量",
        "content_count": "发布内容",
        "posts": "发布内容",
        "follower_count": "粉丝数",
        "followers": "粉丝数",
        "new_followers": "新增粉丝",
        "like_count": "点赞数",
        "likes": "点赞数",
        "comment_count": "评论数",
        "comments": "评论数",
        "share_count": "分享数",
        "shares": "分享数",
        "profile_visit_count": "主页访问",
        "unfollow_count": "取关粉丝",
        "engagement_rate": "互动率",
        "cover_click_rate": "封面点击率",
    }
    rendered: list[str] = []
    missing: list[str] = []
    used_labels: set[str] = set()
    for key, label in labels.items():
        if label in used_labels or key not in metrics:
            continue
        raw = metrics[key]
        value = raw.get("value") if isinstance(raw, dict) else raw
        if value is None:
            missing.append(label)
            used_labels.add(label)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if key == "engagement_rate":
            formatted = f"{value * 100:.2f}%" if value <= 1 else f"{value:.2f}%"
        elif isinstance(value, float) and not value.is_integer():
            formatted = f"{value:,.2f}"
        else:
            formatted = f"{int(value):,}"
        metadata: list[str] = []
        if isinstance(raw, dict):
            source = {
                "platform_export": "平台导出",
                "derived": "系统计算",
            }.get(str(raw.get("source") or ""))
            if source:
                metadata.append(source)
            observed_at = raw.get("observed_at")
            if isinstance(observed_at, str) and observed_at:
                metadata.append(f"观测于 {observed_at[:10]}")
        suffix = f"（{'，'.join(metadata)}）" if metadata else ""
        if len(rendered) < 6:
            rendered.append(f"{label}：{formatted}{suffix}")
        used_labels.add(label)

    coverage_labels = {
        "account_metrics": "账号整体指标",
        "content_metrics": "内容表现指标",
        "content_identity": "内容身份数据",
        "audience": "受众画像",
        "benchmarks": "行业基准",
    }
    coverage = data.get("coverage")
    if isinstance(coverage, dict):
        for key, label in coverage_labels.items():
            if coverage.get(key) == "missing" and label not in missing:
                missing.append(label)

    source_names: list[str] = []
    sources = data.get("sources")
    if isinstance(sources, list):
        for source_item in sources:
            if not isinstance(source_item, dict):
                continue
            source_kind = str(source_item.get("source_kind") or "")
            source_label = {
                "platform_export": "平台导出",
                "derived": "系统计算",
            }.get(source_kind, source_kind)
            batch_id = source_item.get("batch_id")
            if source_label and batch_id is not None:
                source_label = f"{source_label}批次 #{batch_id}"
            if source_label and source_label not in source_names:
                source_names.append(source_label)
    if not source_names:
        for raw in metrics.values():
            if not isinstance(raw, dict):
                continue
            source_label = {
                "platform_export": "平台导出",
                "derived": "系统计算",
            }.get(str(raw.get("source") or ""))
            if source_label and source_label not in source_names:
                source_names.append(source_label)

    lines = [f"数据周期：{period_text}"]
    lines.append(f"数据来源：{'；'.join(source_names)}" if source_names else "数据来源：缺失")
    lines.append(f"已有指标：{'；'.join(rendered)}" if rendered else "已有指标：暂无")
    lines.append(f"缺失数据：{'、'.join(missing)}" if missing else "缺失数据：未发现")
    return "\n".join(lines)
