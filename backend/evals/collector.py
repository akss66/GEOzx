from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    Event,
    SkillRun,
)
from evals.models import EvaluationObservation

_ANSWER_SCALAR_FIELDS = (
    "artifact_type",
    "answerability",
    "title",
    "summary",
)
_PERIOD_FIELDS = ("start", "end", "days", "cutoff", "data_cutoff")
_FACT_FIELDS = (
    "metric_code",
    "current_value",
    "previous_value",
    "change_value",
    "change_rate",
    "unit",
    "label",
)
_RECOMMENDATION_FIELDS = (
    "action",
    "metric",
    "observation_days",
    "rationale",
    "priority",
)
_EVIDENCE_FIELDS = (
    "account_id",
    "metric_code",
    "value",
    "unit",
    "source_id",
    "source_type",
    "period_start",
    "period_end",
)
_QUERY_SKILL_CODES = frozenset({"account_data_query", "account.data_context"})


class EvaluationScopeError(RuntimeError):
    pass


def _enum_value(value: object) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _decimal_number(value: Decimal | int | float | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _duration_ms(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    return max(0, int((finished_at - started_at).total_seconds() * 1_000))


def _copy_fields(item: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: item[field] for field in fields if field in item}


def _safe_list_of_mappings(value: object, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_copy_fields(item, fields) for item in value if isinstance(item, Mapping)]


def _safe_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _sanitize_evidence(value: object) -> tuple[dict[str, Any], ...]:
    return tuple(_safe_list_of_mappings(value, _EVIDENCE_FIELDS))


def _sanitize_answer_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = _copy_fields(payload, _ANSWER_SCALAR_FIELDS)
    period = payload.get("period")
    if isinstance(period, Mapping):
        sanitized["period"] = _copy_fields(period, _PERIOD_FIELDS)
    sanitized["claims"] = _safe_string_list(payload.get("claims"))
    sanitized["key_facts"] = _safe_list_of_mappings(payload.get("key_facts"), _FACT_FIELDS)
    sanitized["recommendations"] = _safe_list_of_mappings(
        payload.get("recommendations"),
        _RECOMMENDATION_FIELDS,
    )
    for field in ("highlights", "issues"):
        if field in payload:
            sanitized[field] = _safe_string_list(payload.get(field))
    return sanitized


async def _scoped_turn_and_thread(
    session: AsyncSession,
    *,
    user_id: int,
    account_id: int,
    thread_id: int,
    turn_id: int,
) -> tuple[ConversationTurn, ConversationThread]:
    turn = await session.scalar(
        select(ConversationTurn).where(
            ConversationTurn.id == turn_id,
            ConversationTurn.thread_id == thread_id,
            ConversationTurn.created_by_id == user_id,
        )
    )
    if turn is None:
        raise EvaluationScopeError("evaluation turn is outside requested user scope")
    thread = await session.scalar(
        select(ConversationThread).where(
            ConversationThread.id == thread_id,
            ConversationThread.org_id == turn.org_id,
            ConversationThread.created_by_id == user_id,
            ConversationThread.account_id == account_id,
        )
    )
    if thread is None:
        raise EvaluationScopeError("evaluation thread is outside requested account scope")
    return turn, thread


async def _scoped_run(
    session: AsyncSession,
    *,
    turn: ConversationTurn,
    user_id: int,
) -> AgentRun:
    run = await session.scalar(
        select(AgentRun)
        .where(
            AgentRun.org_id == turn.org_id,
            AgentRun.requested_by_id == user_id,
            AgentRun.thread_id == turn.thread_id,
            AgentRun.turn_id == turn.id,
        )
        .order_by(AgentRun.id.desc())
    )
    if run is None:
        raise EvaluationScopeError("evaluation run is outside requested turn scope")
    return run


async def _scoped_skill_runs(
    session: AsyncSession,
    *,
    turn: ConversationTurn,
    run: AgentRun,
) -> tuple[SkillRun, ...]:
    rows = await session.scalars(
        select(SkillRun)
        .where(
            SkillRun.org_id == turn.org_id,
            SkillRun.thread_id == turn.thread_id,
            SkillRun.turn_id == turn.id,
            SkillRun.run_id == run.id,
        )
        .order_by(SkillRun.id)
    )
    return tuple(rows)


async def _scoped_tool_calls(
    session: AsyncSession,
    *,
    turn: ConversationTurn,
    run: AgentRun,
) -> tuple[AgentToolCall, ...]:
    statement = (
        select(AgentToolCall)
        .options(selectinload(AgentToolCall.attempts))
        .where(
            AgentToolCall.org_id == turn.org_id,
            AgentToolCall.thread_id == turn.thread_id,
            AgentToolCall.turn_id == turn.id,
        )
        .order_by(AgentToolCall.id)
    )
    if run.task_id is not None:
        statement = statement.where(AgentToolCall.task_id == run.task_id)
    else:
        statement = statement.where(AgentToolCall.task_id.is_(None))
    rows = await session.scalars(statement)
    return tuple(rows)


async def _scoped_invocations(
    session: AsyncSession,
    *,
    turn: ConversationTurn,
    run: AgentRun,
) -> tuple[AgentInvocation, ...]:
    if run.task_id is None:
        return ()
    rows = await session.scalars(
        select(AgentInvocation)
        .where(
            AgentInvocation.task_id == run.task_id,
            AgentInvocation.run_id == run.id,
            AgentInvocation.thread_id == turn.thread_id,
            AgentInvocation.turn_id == turn.id,
        )
        .order_by(AgentInvocation.step_key, AgentInvocation.attempt, AgentInvocation.id)
    )
    return tuple(rows)


async def _scoped_tool_events(
    session: AsyncSession,
    *,
    turn: ConversationTurn,
    run: AgentRun,
    account_id: int,
) -> tuple[Event, ...]:
    rows = await session.scalars(
        select(Event)
        .where(
            Event.type == "brain.runtime.tool_completed",
            Event.thread_id == turn.thread_id,
            Event.turn_id == turn.id,
            Event.run_id == run.id,
        )
        .order_by(Event.sequence, Event.id)
    )
    scoped: list[Event] = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, Mapping) else {}
        if payload.get("org_id") != turn.org_id or payload.get("account_id") != account_id:
            continue
        scoped.append(row)
    return tuple(scoped)


async def _scoped_deliverables(
    session: AsyncSession,
    *,
    turn: ConversationTurn,
    run: AgentRun,
    user_id: int,
    account_id: int,
) -> tuple[Deliverable, ...]:
    rows = await session.scalars(
        select(Deliverable)
        .join(ContentItem, ContentItem.id == Deliverable.content_item_id)
        .where(
            Deliverable.thread_id == turn.thread_id,
            Deliverable.turn_id == turn.id,
            Deliverable.run_id == run.id,
            ContentItem.created_by_id == user_id,
            ContentItem.account_id == account_id,
        )
        .order_by(Deliverable.version.desc(), Deliverable.id.desc())
    )
    return tuple(rows)


def _preferred_payload(
    deliverables: tuple[Deliverable, ...],
    skill_runs: tuple[SkillRun, ...],
) -> Mapping[str, Any]:
    payloads = [item.payload for item in deliverables if isinstance(item.payload, Mapping)]
    deliverable_payload = next(
        (
            payload
            for payload in payloads
            if payload.get("artifact_type") == "account_analysis_answer"
        ),
        payloads[0] if payloads else {},
    )
    if deliverable_payload:
        return deliverable_payload
    output_payloads = [
        row.output_snapshot
        for row in reversed(skill_runs)
        if isinstance(row.output_snapshot, Mapping) and row.output_snapshot
    ]
    return output_payloads[0] if output_payloads else {}


def _tool_observations(tool_calls: tuple[AgentToolCall, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "tool_code": call.tool_code,
            "status": call.status,
            "latency_ms": call.latency_ms,
            "retry_count": max(0, len(call.attempts) - 1),
            "side_effect_level": call.side_effect_level,
            "requires_human_confirmation": call.requires_human_confirmation,
        }
        for call in tool_calls
    )


def _effective_tool_observations(
    tool_calls: tuple[AgentToolCall, ...],
    tool_events: tuple[Event, ...],
) -> tuple[dict[str, Any], ...]:
    persisted = _tool_observations(tool_calls)
    persisted_codes = {str(item["tool_code"]) for item in persisted}
    from_events: list[dict[str, Any]] = []
    for event in tool_events:
        payload = event.payload if isinstance(event.payload, Mapping) else {}
        tool_code = payload.get("tool_code")
        if not isinstance(tool_code, str) or not tool_code.strip():
            continue
        normalized_code = tool_code.strip()
        if normalized_code in persisted_codes:
            continue
        from_events.append(
            {
                "tool_code": normalized_code,
                "status": "completed",
                "latency_ms": None,
                "retry_count": 0,
                "side_effect_level": None,
                "requires_human_confirmation": None,
            }
        )
        persisted_codes.add(normalized_code)
    return persisted + tuple(from_events)


def _skill_observations(skill_runs: tuple[SkillRun, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "skill_code": row.skill_code,
            "skill_version": row.skill_version,
            "status": row.status,
            "quality_score": (float(row.quality_score) if row.quality_score is not None else None),
            "error_code": row.error_code,
        }
        for row in skill_runs
    )


def _expert_observations(
    invocations: tuple[AgentInvocation, ...],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "agent_code": _enum_value(row.agent_code),
            "agent_name": row.agent_name,
            "status": _enum_value(row.status),
            "attempt": row.attempt,
            "model": row.model,
            "token_count": row.token_count,
            "cost": _decimal_number(row.cost),
            "latency_ms": _duration_ms(row.started_at, row.finished_at),
        }
        for row in invocations
    )


def _model_metadata(invocations: tuple[AgentInvocation, ...]) -> dict[str, Any]:
    return {
        "models": sorted({row.model for row in invocations if row.model}),
        "total_tokens": sum(row.token_count for row in invocations),
        "total_cost": sum((_decimal_number(row.cost) for row in invocations), start=0.0),
    }


async def collect_observation(
    session: AsyncSession,
    *,
    case_id: str,
    user_id: int,
    account_id: int,
    thread_id: int,
    turn_id: int,
) -> EvaluationObservation:
    turn, _thread = await _scoped_turn_and_thread(
        session,
        user_id=user_id,
        account_id=account_id,
        thread_id=thread_id,
        turn_id=turn_id,
    )
    run = await _scoped_run(session, turn=turn, user_id=user_id)
    skill_runs = await _scoped_skill_runs(session, turn=turn, run=run)
    tool_calls = await _scoped_tool_calls(session, turn=turn, run=run)
    tool_events = await _scoped_tool_events(
        session,
        turn=turn,
        run=run,
        account_id=account_id,
    )
    invocations = await _scoped_invocations(session, turn=turn, run=run)
    deliverables = await _scoped_deliverables(
        session,
        turn=turn,
        run=run,
        user_id=user_id,
        account_id=account_id,
    )
    payload = _preferred_payload(deliverables, skill_runs)
    intent = turn.intent if isinstance(turn.intent, Mapping) else {}
    route_skill_code = intent.get("skill_code")
    if route_skill_code is None and skill_runs:
        route_skill_code = skill_runs[-1].skill_code

    answer_payload = _sanitize_answer_payload(payload)
    evidence_refs = _sanitize_evidence(payload.get("evidence_refs"))
    terminal_states = {"turn": turn.status, "run": run.status}
    if skill_runs:
        terminal_states["skill"] = skill_runs[-1].status

    return EvaluationObservation(
        case_id=case_id,
        org_id=turn.org_id,
        user_id=user_id,
        account_id=account_id,
        thread_id=thread_id,
        turn_id=turn_id,
        route_mode=(str(intent.get("mode")) if intent.get("mode") is not None else None),
        route_skill_code=(str(route_skill_code) if route_skill_code is not None else None),
        tool_calls=_effective_tool_observations(tool_calls, tool_events),
        skill_runs=_skill_observations(skill_runs),
        expert_invocations=_expert_observations(invocations),
        evidence_refs=evidence_refs,
        answer_payload=answer_payload,
        final_answer=turn.assistant_response or "",
        terminal_states=terminal_states,
        timings_ms={
            "route": turn.route_ms,
            "first_token": turn.first_token_ms,
            "completion": turn.completion_ms,
            "total": turn.total_ms,
        },
        model_metadata=_model_metadata(invocations),
    )
