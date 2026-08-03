"""Atomic convergence for the Turn, Run, SkillRun, and BrainTask ledgers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import record_runtime_event_once, runtime_event_idempotency_key
from app.models import (
    AgentRun,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    Event,
    SkillRun,
)
from app.models.enums import AgentCode, BrainTaskStatus
from app.orchestrator.agent_identity import OPERATIONS_BRAIN_DISPLAY_NAME
from app.services.turn_observability import apply_turn_closure_metrics

TURN_STATUSES = frozenset(
    {
        "queued",
        "running",
        "retry_wait",
        "waiting_permission",
        "waiting_decision",
        "waiting_user",
        "completed",
        "blocked",
        "failed",
        "dead_letter",
        "cancelled",
        "stopped",
    }
)
RUN_STATUSES = TURN_STATUSES | {"claimed", "waiting_predecessor"}
SKILL_RUN_STATUSES = frozenset(
    {
        "running",
        "retry_wait",
        "waiting_permission",
        "needs_review",
        "completed",
        "blocked",
        "failed",
        "cancelled",
        "stopped",
    }
)

ACTIVE_STATUSES = frozenset({"claimed", "waiting_predecessor", "queued", "running", "retry_wait"})
PAUSED_STATUSES = frozenset({"waiting_permission", "waiting_decision", "waiting_user", "stopped"})
TERMINAL_STATUSES = frozenset({"completed", "blocked", "failed", "dead_letter", "cancelled"})


@dataclass(frozen=True)
class RuntimeEventSpec:
    """One extra durable event committed with a runtime state transition."""

    event_type: str
    semantic_key: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class RuntimeStateScope:
    """Stable IDs and delivery metadata for one owned runtime scope."""

    run_id: int
    turn_id: int | None = None
    skill_run_id: int | None = None
    task_id: int | None = None
    account_id: int | None = None
    project_id: int | None = None
    content_item_id: int | None = None
    result_payload: dict[str, Any] | None = None
    skill_output_snapshot: dict[str, Any] | None = None
    skill_status_override: str | None = None
    intent: dict[str, Any] | None = None
    error_detail: str | None = None
    response_streamed: bool = False
    stream_seq_start: int = 0
    extra_events: tuple[RuntimeEventSpec, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RuntimeStateClosure:
    """The effective persisted state after first-terminal-wins convergence."""

    status: str
    turn: ConversationTurn | None
    run: AgentRun
    skill_run: SkillRun | None
    task: BrainTask | None


async def close_runtime_state(
    session: AsyncSession,
    *,
    scope: RuntimeStateScope,
    status: str,
    message: str,
    error_code: str | None = None,
) -> RuntimeStateClosure:
    """Lock, validate, and update all present runtime ledgers in one transaction."""

    if status not in RUN_STATUSES:
        raise ValueError(f"unsupported runtime status: {status}")
    if not message.strip():
        raise ValueError("runtime state message must not be empty")

    broadcasts: list[tuple[Event, str]] = []
    try:
        with session.no_autoflush:
            run = await session.scalar(
                select(AgentRun)
                .where(AgentRun.id == scope.run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if run is None:
                raise ValueError(f"AgentRun not found: {scope.run_id}")

            turn_id = scope.turn_id if scope.turn_id is not None else run.turn_id
            task_id = scope.task_id if scope.task_id is not None else run.task_id
            turn = (
                await session.scalar(
                    select(ConversationTurn)
                    .where(ConversationTurn.id == turn_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if turn_id is not None
                else None
            )
            skill_run = (
                await session.scalar(
                    select(SkillRun)
                    .where(SkillRun.id == scope.skill_run_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if scope.skill_run_id is not None
                else None
            )
            task = (
                await session.scalar(
                    select(BrainTask)
                    .where(BrainTask.id == task_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if task_id is not None
                else None
            )

        _validate_scope(
            scope=scope,
            run=run,
            turn=turn,
            skill_run=skill_run,
            task=task,
        )

        replaying_terminal = run.status in TERMINAL_STATUSES
        previous_turn_status = turn.status if turn is not None else None
        terminal_state_mismatch = (
            replaying_terminal
            and turn is not None
            and turn.status not in TERMINAL_STATUSES
        )
        effective_status, effective_message, effective_error, result_payload = _first_terminal_wins(
            run=run,
            turn=turn,
            requested_status=status,
            requested_message=message,
            requested_error=error_code,
            requested_payload=scope.result_payload,
        )
        now = datetime.now(UTC)
        run.status = effective_status
        run.phase = effective_status
        run.error_code = effective_error
        run.error_detail = scope.error_detail
        if result_payload is not None:
            run.result_payload = result_payload

        family = runtime_status_family(effective_status)
        if family != "active" or effective_status == "retry_wait":
            run.lease_owner = None
            run.leased_until = None
        if family == "terminal":
            run.finished_at = run.finished_at or now
            run.next_retry_at = None

        if turn is not None:
            turn.status = _turn_status(effective_status)
            if scope.intent is not None:
                turn.intent = scope.intent
            if _writes_user_message(effective_status):
                turn.assistant_response = effective_message
            if runtime_status_family(effective_status) in {"paused", "terminal"}:
                apply_turn_closure_metrics(
                    turn,
                    now=now,
                    writes_user_message=_writes_user_message(effective_status),
                )

        preserve_terminal_skill = False
        if skill_run is not None:
            preserve_terminal_skill = (
                skill_run.status in {"completed", "blocked", "failed", "cancelled"}
                and family == "active"
            )
            if not preserve_terminal_skill:
                skill_run.status = _skill_status(
                    scope.skill_status_override or effective_status
                )
                skill_run.error_code = effective_error
                if scope.skill_output_snapshot is not None and not replaying_terminal:
                    skill_run.output_snapshot = scope.skill_output_snapshot

        if task is not None and not (preserve_terminal_skill and family == "active"):
            task.status = brain_task_status(effective_status)
            task.current_focus = effective_message[:500]
            if task.status == BrainTaskStatus.COMPLETED:
                task.progress = 100
            elif task.status == BrainTaskStatus.FAILED:
                task.progress = 0

        if turn is not None and _writes_user_message(effective_status):
            account_id, project_id = await _delivery_scope(
                session,
                scope=scope,
                turn=turn,
            )
            broadcasts = await _record_delivery_events(
                session,
                scope=scope,
                turn=turn,
                run=run,
                skill_run=skill_run,
                task=task,
                account_id=account_id,
                project_id=project_id,
                status=effective_status,
                message=effective_message,
                error_code=effective_error,
                diagnostic_events=tuple(
                    event
                    for event in (
                        RuntimeEventSpec(
                            event_type="brain.runtime.terminal_state_reconciled",
                            semantic_key="terminal-state-reconciled",
                            payload={
                                "run_status": effective_status,
                                "previous_turn_status": previous_turn_status,
                            },
                        )
                        if terminal_state_mismatch
                        else None,
                        RuntimeEventSpec(
                            event_type="brain.runtime.skill_stage_timeout",
                            semantic_key=f"skill-stage-timeout:{skill_run.id}",
                            payload={
                                "skill_run_id": skill_run.id,
                                "error_code": effective_error,
                            },
                        )
                        if skill_run is not None
                        and effective_error is not None
                        and "TIMEOUT" in effective_error.upper()
                        else None,
                    )
                    if event is not None
                ),
            )
        elif turn is None and effective_status in {
            "failed",
            "dead_letter",
            "cancelled",
        }:
            broadcasts = await _record_run_only_terminal_event(
                session,
                scope=scope,
                run=run,
                task=task,
                status=effective_status,
                message=effective_message,
                error_code=effective_error,
            )

        await session.commit()
    except Exception:
        await session.rollback()
        raise

    await _publish_delivery_events(
        broadcasts,
        message=effective_message,
        response_streamed=scope.response_streamed,
    )
    return RuntimeStateClosure(
        status=effective_status,
        turn=turn,
        run=run,
        skill_run=skill_run,
        task=task,
    )


def runtime_status_family(status: str) -> str:
    if status in ACTIVE_STATUSES:
        return "active"
    if status in PAUSED_STATUSES:
        return "paused"
    if status in TERMINAL_STATUSES:
        return "terminal"
    raise ValueError(f"unsupported runtime status: {status}")


def brain_task_status(status: str) -> BrainTaskStatus:
    if status in {"claimed", "queued", "running", "retry_wait"}:
        return BrainTaskStatus.RUNNING
    if status.startswith("waiting_") or status == "stopped":
        return BrainTaskStatus.PENDING_CONFIRMATION
    if status == "completed":
        return BrainTaskStatus.COMPLETED
    if status in {"blocked", "failed", "dead_letter", "cancelled"}:
        return BrainTaskStatus.FAILED
    raise ValueError(f"unsupported BrainTask runtime status: {status}")


def _validate_scope(
    *,
    scope: RuntimeStateScope,
    run: AgentRun,
    turn: ConversationTurn | None,
    skill_run: SkillRun | None,
    task: BrainTask | None,
) -> None:
    if scope.turn_id is not None and turn is None:
        raise ValueError("runtime state Turn ownership is missing")
    if scope.skill_run_id is not None and skill_run is None:
        raise ValueError("runtime state SkillRun ownership is missing")
    if scope.task_id is not None and task is None:
        raise ValueError("runtime state BrainTask ownership is missing")
    if turn is not None and (
        run.turn_id != turn.id or run.thread_id != turn.thread_id or run.org_id != turn.org_id
    ):
        raise ValueError("runtime state Turn and AgentRun ownership do not match")
    if task is not None and (run.task_id != task.id or run.org_id != task.org_id):
        raise ValueError("runtime state BrainTask and AgentRun ownership do not match")
    if skill_run is not None and (
        skill_run.run_id != run.id
        or skill_run.turn_id != run.turn_id
        or skill_run.thread_id != run.thread_id
        or skill_run.org_id != run.org_id
        or skill_run.task_id != (task.id if task is not None else None)
    ):
        raise ValueError("runtime state SkillRun ownership does not match")


def _first_terminal_wins(
    *,
    run: AgentRun,
    turn: ConversationTurn | None,
    requested_status: str,
    requested_message: str,
    requested_error: str | None,
    requested_payload: dict[str, Any] | None,
) -> tuple[str, str, str | None, dict[str, Any] | None]:
    if run.status not in TERMINAL_STATUSES:
        return (
            requested_status,
            requested_message,
            requested_error,
            requested_payload,
        )
    persisted_message = (
        turn.assistant_response
        if turn is not None and turn.assistant_response
        else requested_message
    )
    persisted_payload = dict(run.result_payload or {}) or requested_payload
    return run.status, persisted_message, run.error_code, persisted_payload


def _turn_status(status: str) -> str:
    if status in {"claimed", "waiting_predecessor"}:
        return "queued"
    if status not in TURN_STATUSES:
        raise ValueError(f"unsupported ConversationTurn runtime status: {status}")
    return status


def _skill_status(status: str) -> str:
    mapped = {
        "claimed": "running",
        "waiting_predecessor": "running",
        "queued": "running",
        "waiting_decision": "waiting_permission",
        "waiting_user": "waiting_permission",
        "dead_letter": "failed",
    }.get(status, status)
    if mapped not in SKILL_RUN_STATUSES:
        raise ValueError(f"unsupported SkillRun runtime status: {status}")
    return mapped


def _writes_user_message(status: str) -> bool:
    return status in PAUSED_STATUSES or status in TERMINAL_STATUSES


async def _delivery_scope(
    session: AsyncSession,
    *,
    scope: RuntimeStateScope,
    turn: ConversationTurn,
) -> tuple[int, int | None]:
    if scope.account_id is not None:
        return scope.account_id, scope.project_id
    thread = await session.scalar(
        select(ConversationThread).where(ConversationThread.id == turn.thread_id)
    )
    if thread is None:
        raise ValueError("runtime state ConversationThread ownership is missing")
    project_id = scope.project_id if scope.project_id is not None else thread.project_id
    return thread.account_id, project_id


async def _record_delivery_events(
    session: AsyncSession,
    *,
    scope: RuntimeStateScope,
    turn: ConversationTurn,
    run: AgentRun,
    skill_run: SkillRun | None,
    task: BrainTask | None,
    account_id: int,
    project_id: int | None,
    status: str,
    message: str,
    error_code: str | None,
    diagnostic_events: tuple[RuntimeEventSpec, ...] = (),
) -> list[tuple[Event, str]]:
    lineage = {
        "task_id": task.id if task is not None else None,
        "thread_id": turn.thread_id,
        "turn_id": turn.id,
        **({"skill_run_id": skill_run.id} if skill_run is not None else {}),
    }
    specs = [
        *scope.extra_events,
        *diagnostic_events,
        RuntimeEventSpec(
            event_type="brain.runtime.message_done",
            semantic_key=f"runtime-state-message:{status}",
            payload={
                "message": message,
                "content": message,
                "message_id": f"{run.client_message_id}:{AgentCode.DECISION.value}:1",
                "agent_code": AgentCode.DECISION.value,
                "agent_name": OPERATIONS_BRAIN_DISPLAY_NAME,
                "model": "system",
                "status": status,
                "stream_seq": scope.stream_seq_start,
            },
        ),
        RuntimeEventSpec(
            event_type={
                "completed": "brain.runtime.completed",
                "failed": "brain.runtime.failed",
                "dead_letter": "brain.runtime.failed",
                "cancelled": "brain.runtime.cancelled",
                "stopped": "brain.runtime.generation_stopped",
            }.get(status, "brain.runtime.turn_paused"),
            semantic_key=f"runtime-state:{status}",
            payload={
                "message": message,
                "status": status,
                **({"error_code": error_code} if error_code else {}),
            },
        ),
    ]
    broadcasts: list[tuple[Event, str]] = []
    for spec in specs:
        event, created = await record_runtime_event_once(
            session,
            org_id=turn.org_id,
            account_id=account_id,
            run_id=run.id,
            client_message_id=run.client_message_id,
            event_type=spec.event_type,
            semantic_key=spec.semantic_key,
            payload={**lineage, **spec.payload},
            content_item_id=scope.content_item_id,
            project_id=project_id,
        )
        if not created:
            continue
        event.thread_id = turn.thread_id
        event.turn_id = turn.id
        event.run_id = run.id
        event.skill_run_id = skill_run.id if skill_run is not None else None
        broadcasts.append((event, spec.event_type))
    return broadcasts


async def _publish_delivery_events(
    broadcasts: list[tuple[Event, str]],
    *,
    message: str,
    response_streamed: bool,
) -> None:
    # Keep the established patch/observer boundary used by the realtime runtime.
    from app.orchestrator import brain_runtime

    for event, event_type in broadcasts:
        payload = dict(event.payload or {})
        await brain_runtime.publish_realtime_event(
            event_type,
            payload,
            content_item_id=event.content_item_id,
            project_id=event.project_id,
            event_id=event.id,
        )
async def _record_run_only_terminal_event(
    session: AsyncSession,
    *,
    scope: RuntimeStateScope,
    run: AgentRun,
    task: BrainTask | None,
    status: str,
    message: str,
    error_code: str | None,
) -> list[tuple[Event, str]]:
    event_type = {
        "failed": "brain.runtime.failed",
        "dead_letter": "brain.runtime.failed",
        "cancelled": "brain.runtime.cancelled",
    }[status]
    account_id = scope.account_id or 0
    semantic_key = f"runtime-state:{status}"
    idempotency_key = runtime_event_idempotency_key(
        org_id=run.org_id,
        account_id=account_id,
        run_id=run.id,
        client_message_id=run.client_message_id,
        event_type=event_type,
        semantic_key=semantic_key,
    )
    existing = await session.scalar(select(Event).where(Event.idempotency_key == idempotency_key))
    if existing is not None:
        return []
    payload = {
        "task_id": task.id if task is not None else None,
        "agent_run_id": run.id,
        **({"error_code": error_code} if error_code else {}),
        "message": message,
    }
    recovery_action = (scope.result_payload or {}).get("recovery_action")
    if isinstance(recovery_action, str) and recovery_action:
        payload["recovery_action"] = recovery_action
    try:
        async with session.begin_nested():
            event = Event(
                type=event_type,
                content_item_id=(
                    scope.content_item_id
                    if scope.content_item_id is not None
                    else task.content_item_id
                    if task is not None
                    else None
                ),
                project_id=scope.project_id,
                run_id=run.id,
                payload=payload,
                idempotency_key=idempotency_key,
            )
            session.add(event)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(Event).where(Event.idempotency_key == idempotency_key)
        )
        if existing is None:
            raise
        return []
    event.run_id = run.id
    return [(event, event_type)]
