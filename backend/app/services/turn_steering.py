"""Conservative deterministic routing and durable effects for turn steering."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentRun,
    ConversationThread,
    ConversationTurn,
    RunRevision,
    SkillRun,
    User,
)
from app.orchestrator.runtime_scope import RuntimeScope
from app.orchestrator.step_dependencies import ConstraintPath, build_invalidation_plan
from app.schemas.conversation import CreateConversationTurnRequest, TurnSteeringMode
from app.services.agent_runs import (
    mark_agent_run_queued_record,
    queue_agent_run_behind_task_record,
    request_agent_run_cancel_record,
)
from app.services.capability_request import extract_structured_constraints
from app.services.run_revisions import create_revision_record
from app.services.turn_events import TurnEventScope, append_turn_event

_TERMINAL_STATUSES = {
    "completed",
    "blocked",
    "failed",
    "dead_letter",
    "cancelled",
    "stopped",
}


@dataclass(frozen=True)
class TurnSteeringDecision:
    mode: TurnSteeringMode
    target_turn_id: int | None
    explanation: str


@dataclass(frozen=True)
class ResolvedTurnSteering:
    decision: TurnSteeringDecision
    target_turn: ConversationTurn | None = None
    target_run: AgentRun | None = None
    legacy_replay: bool = False


def classify_turn_steering(
    message: str,
    *,
    active_turn_id: int | None,
) -> TurnSteeringDecision:
    """Classify only explicit steering language; ambiguity fails closed."""

    normalized = " ".join(message.strip().lower().split())
    if active_turn_id is not None and _is_explicit_replacement(normalized):
        return TurnSteeringDecision(
            mode=TurnSteeringMode.REPLACE_GOAL,
            target_turn_id=active_turn_id,
            explanation="已按新目标创建替代任务。",
        )
    if _is_explicit_independent_query(normalized):
        return _independent_decision()
    if active_turn_id is not None and _is_explicit_stop_command(normalized):
        return TurnSteeringDecision(
            mode=TurnSteeringMode.STOP,
            target_turn_id=active_turn_id,
            explanation="已请求停止当前任务。",
        )
    if active_turn_id is not None and _contains_any(
        normalized,
        ("不要讲", "别讲", "补充", "加上", "保持", "同时要"),
    ):
        return TurnSteeringDecision(
            mode=TurnSteeringMode.SUPPLEMENT,
            target_turn_id=active_turn_id,
            explanation="已补充到当前任务的要求中。",
        )
    return _independent_decision()


async def resolve_turn_steering(
    session: AsyncSession,
    user: User,
    thread: ConversationThread,
    request: CreateConversationTurnRequest,
) -> ResolvedTurnSteering:
    """Resolve an explicit or latest active target without crossing thread scope."""

    existing = await session.scalar(
        select(ConversationTurn).where(
            ConversationTurn.thread_id == thread.id,
            ConversationTurn.org_id == user.org_id,
            ConversationTurn.client_message_id == request.client_message_id,
        )
    )
    if existing is not None and existing.steering_mode is None:
        return ResolvedTurnSteering(
            decision=_independent_decision(),
            legacy_replay=True,
        )
    if existing is not None:
        assert existing.steering_mode is not None
        mode = TurnSteeringMode(existing.steering_mode)
        target_turn, target_run = await _load_persisted_target(
            session,
            thread,
            existing.target_turn_id,
        )
        return ResolvedTurnSteering(
            decision=TurnSteeringDecision(
                mode=mode,
                target_turn_id=existing.target_turn_id,
                explanation=_explanation(mode),
            ),
            target_turn=target_turn,
            target_run=target_run,
        )

    if request.target_turn_id is not None:
        target_turn, target_run = await _load_target_pair(
            session,
            thread,
            request.target_turn_id,
        )
        if target_turn is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation turn not found",
            )
        if not _is_active_target(target_turn, target_run):
            return ResolvedTurnSteering(decision=_independent_decision())
        decision = classify_turn_steering(
            request.message,
            active_turn_id=target_turn.id,
        )
        if decision.mode is TurnSteeringMode.INDEPENDENT_QUERY:
            return ResolvedTurnSteering(decision=decision)
        return ResolvedTurnSteering(
            decision=decision,
            target_turn=target_turn,
            target_run=target_run,
        )

    probe = classify_turn_steering(request.message, active_turn_id=1)
    if probe.mode is TurnSteeringMode.INDEPENDENT_QUERY:
        return ResolvedTurnSteering(decision=probe)
    target_turn, target_run = await _load_latest_active_target(session, thread)
    decision = classify_turn_steering(
        request.message,
        active_turn_id=target_turn.id if target_turn is not None else None,
    )
    return ResolvedTurnSteering(
        decision=decision,
        target_turn=target_turn if decision.target_turn_id is not None else None,
        target_run=target_run if decision.target_turn_id is not None else None,
    )


def bind_turn_steering(
    turn: ConversationTurn,
    decision: TurnSteeringDecision,
) -> None:
    """Bind immutable server-derived steering lineage to a newly created turn."""

    turn.target_turn_id = decision.target_turn_id
    turn.steering_mode = decision.mode.value


async def apply_turn_steering(
    session: AsyncSession,
    thread: ConversationThread,
    steering_turn: ConversationTurn,
    steering_run: AgentRun,
    resolved: ResolvedTurnSteering,
) -> bool:
    """Apply control effects and return whether the steering run needs enqueue."""

    if resolved.legacy_replay:
        return False
    decision = resolved.decision
    if decision.mode is TurnSteeringMode.INDEPENDENT_QUERY:
        return True
    if decision.mode is TurnSteeringMode.REPLACE_GOAL:
        await _request_target_cancel(session, resolved.target_run)
        await _append_target_event(
            session,
            thread,
            steering_turn,
            resolved,
            message="当前任务已由新目标替代。",
        )
        return True

    if decision.mode is TurnSteeringMode.STOP:
        await _request_target_cancel(session, resolved.target_run)
        await _append_target_event(
            session,
            thread,
            steering_turn,
            resolved,
            message="已请求停止当前任务。",
        )
    else:
        await _append_target_event(
            session,
            thread,
            steering_turn,
            resolved,
            message="已收到补充要求。",
        )

        revision_payload = await _create_supplement_revision(
            session,
            thread=thread,
            steering_turn=steering_turn,
            steering_run=steering_run,
            resolved=resolved,
        )
        await complete_control_run_record(
            session,
            thread,
            steering_turn,
            steering_run,
            decision,
            result_fields=revision_payload,
        )
        await session.flush()
        return False

    await complete_control_run_record(
        session,
        thread,
        steering_turn,
        steering_run,
        decision,
    )
    await session.flush()
    return False


async def complete_control_run_record(
    session: AsyncSession,
    thread: ConversationThread,
    steering_turn: ConversationTurn,
    steering_run: AgentRun,
    decision: TurnSteeringDecision,
    *,
    result_fields: dict[str, object] | None = None,
) -> None:
    """Canonically complete a no-model control Run without committing."""

    scope = TurnEventScope(
        org_id=steering_turn.org_id,
        account_id=thread.account_id,
        thread_id=thread.id,
        turn_id=steering_turn.id,
        run_id=steering_run.id,
    )
    metadata = {
        "category": "steering",
        "label": decision.mode.value,
        "source_id": decision.target_turn_id,
    }
    await append_turn_event(
        session,
        scope,
        "turn.received",
        {
            "status": "running",
            "message": "已收到控制指令。",
            "metadata": metadata,
        },
        "control-received",
    )
    now = datetime.now(UTC)
    steering_turn.status = "completed"
    steering_turn.assistant_response = decision.explanation
    steering_run.status = "completed"
    steering_run.phase = "completed"
    steering_run.started_at = steering_run.started_at or now
    steering_run.finished_at = steering_run.finished_at or now
    steering_run.result_payload = {
        "mode": "answer",
        "status": "completed",
        "response": decision.explanation,
        "task_id": None,
        "projections": [],
        "error_code": None,
        **(result_fields or {}),
    }
    await append_turn_event(
        session,
        scope,
        "turn.completed",
        {
            "status": "completed",
            "message": decision.explanation,
            "metadata": metadata,
        },
        "terminal",
    )


def _normalized_supplement_input(
    message: str,
    *,
    source_input: dict[str, object],
) -> tuple[set[ConstraintPath | str], dict[str, object]]:
    normalized = "".join(message.strip().split())
    if normalized in {"保持现有要求不变", "保持不变", "无需修改"}:
        return set(), dict(source_input)
    extracted = extract_structured_constraints(message)
    unsupported_goal_fields = {
        key for key in ("generate_strategy", "requested_output") if key in extracted
    }
    if unsupported_goal_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "UNSUPPORTED_OPERATION_ITERATION_GOAL",
                "message": "当前运营迭代不支持修改输出目标，请创建独立任务。",
                "fields": sorted(unsupported_goal_fields),
            },
        )
    merged_input = dict(source_input)
    mapped: set[ConstraintPath | str] = set()
    offer_terms = _offer_terms_constraint(message)
    if offer_terms is not None:
        raw_constraints = merged_input.get("constraints")
        existing_constraints = list(raw_constraints) if isinstance(raw_constraints, list) else []
        semantic_key = (
            offer_terms["constraint_type"],
            tuple(offer_terms["target_scope"]["item_indexes"]),
        )
        has_equivalent = any(
            isinstance(item, dict)
            and (
                item.get("constraint_type"),
                tuple((item.get("target_scope") or {}).get("item_indexes") or []),
            )
            == semantic_key
            for item in existing_constraints
        )
        if not has_equivalent:
            existing_constraints.append(offer_terms)
            merged_input["constraints"] = existing_constraints
            mapped.add(ConstraintPath.OFFER_TERMS)
    if "days" in extracted:
        cycle_days = int(extracted["days"])
        if merged_input.get("cycle_days") != cycle_days:
            mapped.add(ConstraintPath.TOPIC_REQUIREMENTS)
        merged_input["cycle_days"] = cycle_days
    if "topic_count" in extracted:
        topic_count = int(extracted["topic_count"])
        if merged_input.get("topic_count") != topic_count:
            mapped.add(ConstraintPath.TOPIC_REQUIREMENTS)
        merged_input["topic_count"] = topic_count
    if "duration_seconds" in extracted:
        duration_seconds = int(extracted["duration_seconds"])
        if merged_input.get("script_duration_seconds") != duration_seconds:
            mapped.add(ConstraintPath.SCRIPT_REQUIREMENTS)
        merged_input["script_duration_seconds"] = duration_seconds
    # Unknown free text must never be guessed into a concrete field. A stable
    # unknown marker makes the dependency planner choose safe full recompute.
    if not extracted and offer_terms is None:
        mapped.add("unmapped_supplement")
    return mapped, merged_input


def _offer_terms_constraint(message: str) -> dict[str, Any] | None:
    normalized = "".join(message.strip().split())
    if not (
        normalized.startswith("第一条")
        and any(marker in normalized for marker in ("不要讲价格", "别讲价格", "不能讲价格"))
    ):
        return None
    return {
        "constraint_type": "OFFER_TERMS",
        "raw_requirement": message.strip(),
        "target_scope": {
            "kind": "content_item_indexes",
            "item_indexes": [1],
        },
    }


async def _create_supplement_revision(
    session: AsyncSession,
    *,
    thread: ConversationThread,
    steering_turn: ConversationTurn,
    steering_run: AgentRun,
    resolved: ResolvedTurnSteering,
) -> dict[str, object]:
    source_run = resolved.target_run
    source_turn = resolved.target_turn
    if (
        source_run is None
        or source_turn is None
        or source_run.task_id is None
        or thread.account_id is None
    ):
        return {}
    existing_revision = await session.scalar(
        select(RunRevision)
        .where(
            RunRevision.revision_turn_id == steering_turn.id,
            RunRevision.source_run_id == source_run.id,
            RunRevision.task_id == source_run.task_id,
        )
        .with_for_update()
    )
    if existing_revision is not None:
        existing_run = await session.get(AgentRun, existing_revision.revision_run_id)
        if existing_run is None:
            raise RuntimeError("REVISION_RUN_MISSING")
        return {
            "revision_run_id": existing_run.id,
            "revision_id": existing_revision.id,
            "task_id": source_run.task_id,
            "revision_status": existing_run.status,
        }
    source_skill = await session.scalar(
        select(SkillRun)
        .where(
            SkillRun.run_id == source_run.id,
            SkillRun.task_id == source_run.task_id,
            SkillRun.skill_code == "operation_iteration",
        )
        .order_by(SkillRun.id.desc())
        .limit(1)
        .with_for_update()
    )
    if source_skill is None:
        return {}
    changed, revision_input = _normalized_supplement_input(
        steering_turn.user_input,
        source_input=dict(source_skill.input_snapshot or {}),
    )
    if not changed:
        return {}

    revision_run = AgentRun(
        org_id=steering_turn.org_id,
        requested_by_id=steering_run.requested_by_id,
        task_id=source_run.task_id,
        thread_id=thread.id,
        turn_id=steering_turn.id,
        client_message_id=f"revision-run:{steering_run.id}",
        status="claimed",
        phase="request",
        request_payload={
            "operation": "execute_revision",
            "task_id": source_run.task_id,
            "source_run_id": source_run.id,
            "source_skill_run_id": source_skill.id,
            "message": steering_turn.user_input,
            "client_message_id": steering_turn.client_message_id,
            "requested_skill_code": "operation_iteration",
            "execution_preference": "FORMAL_TASK",
            "structured_input": revision_input,
            "attachment_ids": [],
        },
    )
    session.add(revision_run)
    await session.flush()
    revision_skill = SkillRun(
        org_id=steering_turn.org_id,
        thread_id=thread.id,
        turn_id=steering_turn.id,
        run_id=revision_run.id,
        task_id=source_run.task_id,
        idempotency_key=f"revision:{revision_run.id}:operation_iteration:v1",
        skill_code="operation_iteration",
        skill_version=1,
        status="running",
        input_snapshot=revision_input,
        output_snapshot={},
    )
    session.add(revision_skill)
    await session.flush()
    common_scope = {
        "org_id": steering_turn.org_id,
        "user_id": steering_run.requested_by_id,
        "account_id": thread.account_id,
        "thread_id": thread.id,
        "task_id": source_run.task_id,
    }
    revision = await create_revision_record(
        session,
        source_scope=RuntimeScope(
            **common_scope,
            turn_id=source_turn.id,
            run_id=source_run.id,
            skill_run_id=source_skill.id,
        ),
        revision_scope=RuntimeScope(
            **common_scope,
            turn_id=steering_turn.id,
            run_id=revision_run.id,
            skill_run_id=revision_skill.id,
        ),
        invalidation=build_invalidation_plan("operation_iteration", changed),
    )
    if not isinstance(revision, RunRevision):
        raise RuntimeError("NONEMPTY_SUPPLEMENT_REVISION_MISSING")
    waiting = await queue_agent_run_behind_task_record(
        session,
        revision_run.id,
        task_id=source_run.task_id,
        request_payload={
            **dict(revision_run.request_payload),
            "revision_id": revision.id,
            "revision_skill_run_id": revision_skill.id,
        },
    )
    if waiting:
        revision.status = "waiting_predecessor"
    else:
        await mark_agent_run_queued_record(
            session,
            revision_run.id,
            task_id=source_run.task_id,
            request_payload={
                **dict(revision_run.request_payload),
                "revision_id": revision.id,
                "revision_skill_run_id": revision_skill.id,
            },
        )
    revision_scope = TurnEventScope(
        org_id=steering_turn.org_id,
        account_id=thread.account_id,
        thread_id=thread.id,
        turn_id=steering_turn.id,
        run_id=revision_run.id,
        skill_run_id=revision_skill.id,
    )
    for step in revision.affected_steps:
        await append_turn_event(
            session,
            revision_scope,
            "step.invalidated",
            {
                "revision_id": revision.id,
                "revision_run_id": revision_run.id,
                "task_id": source_run.task_id,
                "step": step,
                "step_key": step,
                "status": "invalidated",
            },
            f"revision:{revision.id}:invalidated:{step}",
        )
    await append_turn_event(
        session,
        revision_scope,
        "run.revision_planned",
        {
            "revision_id": revision.id,
            "revision_run_id": revision_run.id,
            "task_id": source_run.task_id,
            "mode": revision.mode,
            "status": revision.status,
        },
        f"revision:{revision.id}:planned",
    )
    await session.flush()
    return {
        "revision_run_id": revision_run.id,
        "revision_id": revision.id,
        "task_id": source_run.task_id,
        "revision_status": revision_run.status,
    }


async def _load_target_pair(
    session: AsyncSession,
    thread: ConversationThread,
    turn_id: int,
) -> tuple[ConversationTurn | None, AgentRun | None]:
    # Steering always acquires AgentRun before ConversationTurn. Keeping this
    # one global order prevents stop/replace races from deadlocking workers.
    run = await session.scalar(
        select(AgentRun)
        .where(
            AgentRun.org_id == thread.org_id,
            AgentRun.thread_id == thread.id,
            AgentRun.turn_id == turn_id,
        )
        .order_by(AgentRun.id.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    turn = await session.scalar(
        select(ConversationTurn)
        .where(
            ConversationTurn.id == turn_id,
            ConversationTurn.thread_id == thread.id,
            ConversationTurn.org_id == thread.org_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return turn, run


async def _load_latest_active_target(
    session: AsyncSession,
    thread: ConversationThread,
) -> tuple[ConversationTurn | None, AgentRun | None]:
    run = await session.scalar(
        select(AgentRun)
        .where(
            AgentRun.org_id == thread.org_id,
            AgentRun.thread_id == thread.id,
            AgentRun.status.not_in(_TERMINAL_STATUSES),
        )
        .order_by(AgentRun.turn_id.desc(), AgentRun.id.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None or run.turn_id is None:
        return None, None
    turn = await session.scalar(
        select(ConversationTurn)
        .where(
            ConversationTurn.id == run.turn_id,
            ConversationTurn.thread_id == thread.id,
            ConversationTurn.org_id == thread.org_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if turn is None or not _is_active_target(turn, run):
        return None, None
    return turn, run


async def _load_persisted_target(
    session: AsyncSession,
    thread: ConversationThread,
    turn_id: int | None,
) -> tuple[ConversationTurn | None, AgentRun | None]:
    if turn_id is None:
        return None, None
    return await _load_target_pair(session, thread, turn_id)


def _is_active_target(turn: ConversationTurn, run: AgentRun | None) -> bool:
    return (
        turn.status not in _TERMINAL_STATUSES
        and run is not None
        and run.status not in _TERMINAL_STATUSES
    )


async def _request_target_cancel(
    session: AsyncSession,
    run: AgentRun | None,
) -> None:
    if run is not None:
        await request_agent_run_cancel_record(session, run.id)


async def _append_target_event(
    session: AsyncSession,
    thread: ConversationThread,
    steering_turn: ConversationTurn,
    resolved: ResolvedTurnSteering,
    *,
    message: str,
) -> None:
    if resolved.target_turn is None:
        return
    await append_turn_event(
        session,
        TurnEventScope(
            org_id=resolved.target_turn.org_id,
            account_id=thread.account_id,
            thread_id=thread.id,
            turn_id=resolved.target_turn.id,
            run_id=resolved.target_run.id if resolved.target_run else None,
        ),
        "turn.steered",
        {
            "message": message,
            "metadata": {
                "category": "steering",
                "label": resolved.decision.mode.value,
                "source_id": steering_turn.id,
            },
        },
        f"steering:{steering_turn.id}",
    )


def _explanation(mode: TurnSteeringMode) -> str:
    return {
        TurnSteeringMode.SUPPLEMENT: "已补充到当前任务的要求中。",
        TurnSteeringMode.STOP: "已请求停止当前任务。",
        TurnSteeringMode.REPLACE_GOAL: "已按新目标创建替代任务。",
        TurnSteeringMode.INDEPENDENT_QUERY: "已作为新的独立问题处理。",
    }[mode]


def _contains_any(message: str, markers: tuple[str, ...]) -> bool:
    return any(marker in message for marker in markers)


def _is_explicit_independent_query(message: str) -> bool:
    return _contains_any(message, ("顺便", "另外问", "另一个问题", "by the way"))


def _is_explicit_stop_command(message: str) -> bool:
    command = message.strip(" ，。！？!?；;")
    return command in {
        "停止",
        "暂停",
        "停止任务",
        "暂停任务",
        "停止当前任务",
        "暂停当前任务",
        "停止这个任务",
        "暂停这个任务",
        "请停止当前任务",
        "请暂停当前任务",
        "先停一下",
        "停一下",
        "先暂停一下",
        "暂停一下",
        "请先停一下",
        "别继续",
        "别继续了",
        "不要继续",
        "不要继续了",
    }


def _is_explicit_replacement(message: str) -> bool:
    compound_replacement = (
        _contains_any(message, ("改为", "换成", "replace", "instead"))
        and _contains_any(message, ("方案", "目标", "规划", "计划", "方向", "整体"))
    )
    replan = (
        _contains_any(message, ("重新按", "重做"))
        and _contains_any(message, ("方案", "目标", "规划", "计划", "方向", "整体"))
    )
    return compound_replacement or replan


def _independent_decision() -> TurnSteeringDecision:
    return TurnSteeringDecision(
        mode=TurnSteeringMode.INDEPENDENT_QUERY,
        target_turn_id=None,
        explanation="已作为新的独立问题处理。",
    )


__all__ = [
    "ResolvedTurnSteering",
    "TurnSteeringDecision",
    "TurnSteeringMode",
    "apply_turn_steering",
    "bind_turn_steering",
    "classify_turn_steering",
    "complete_control_run_record",
    "resolve_turn_steering",
]
