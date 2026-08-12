"""Single transactional owner for recoverable human turn interrupts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import require_account_access
from app.models import (
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    Event,
    SkillRun,
    ToolExecutionAttempt,
    TurnInterrupt,
    User,
)
from app.models.enums import BrainTaskStatus
from app.orchestrator.skills.wechat_article_production import resolve_missing_primary_cta
from app.services.composite_skill_runs import lock_composite_finish_approval
from app.services.runtime_locking import (
    RuntimeRootLock,
    lock_runtime_root_scope,
    require_runtime_root_lock,
)
from app.services.runtime_state import (
    RuntimePublishIntent,
    RuntimeStateScope,
    close_runtime_state,
)
from app.services.skill_approvals import (
    SkillApprovalConflict,
    finalize_skill_finish_approval,
)
from app.services.turn_events import TurnEventScope, append_turn_event

_INTERRUPT_KINDS = frozenset({"clarification", "approval", "manual_pause"})


@dataclass(frozen=True)
class InterruptRequestResult:
    interrupt: TurnInterrupt
    publish_intents: tuple[RuntimePublishIntent, ...] = ()


@dataclass(frozen=True)
class InterruptDispatchIntent:
    run_id: int


@dataclass(frozen=True)
class InterruptResolutionResult:
    interrupt: TurnInterrupt
    run: AgentRun
    dispatch_intent: InterruptDispatchIntent | None
    publish_intents: tuple[RuntimePublishIntent, ...] = ()
    replay_runtime_events: bool = False


@dataclass(frozen=True)
class InterruptStopResult:
    run_id: int
    thread_id: int
    turn_id: int
    client_message_id: str
    publish_intents: tuple[RuntimePublishIntent, ...] = ()


@dataclass(frozen=True)
class _DiscoveredRuntime:
    run: AgentRun
    thread: ConversationThread
    turn: ConversationTurn
    task: BrainTask | None
    root_skill_run_id: int | None
    child_skill_run_ids: tuple[int, ...]
    invocation_ids: tuple[int, ...]
    tool_call_ids: tuple[int, ...]
    attempt_ids: tuple[int, ...]
    source_tool: AgentToolCall | None


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interrupt not found")


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message},
    )


def _invalid_resolution(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "INTERRUPT_RESOLUTION_INVALID", "message": message},
    )


async def request_interrupt(
    session: AsyncSession,
    *,
    user: User,
    run_id: int,
    kind: str,
    semantic_key: str,
    public_message: str,
    response_schema: dict,
    action_label: str | None = None,
    skill_run_id: int | None = None,
    source_type: str | None = None,
    source_id: int | None = None,
    source_version: int | None = None,
    prelocked: RuntimeRootLock | None = None,
) -> InterruptRequestResult:
    """Create one durable pause without committing the caller transaction."""

    if kind not in _INTERRUPT_KINDS:
        raise ValueError(f"unsupported interrupt kind: {kind}")
    if not semantic_key.strip() or not public_message.strip():
        raise ValueError("interrupt semantic key and public message are required")
    discovered = await _discover_runtime(
        session,
        user=user,
        run_id=run_id,
        kind=kind,
        skill_run_id=skill_run_id,
        source_type=source_type,
        source_id=source_id,
        source_version=source_version,
    )
    runtime_lock = prelocked or await lock_runtime_root_scope(
        session,
        run_id=discovered.run.id,
        expected_turn_id=discovered.turn.id,
        expected_task_id=discovered.run.task_id,
        root_skill_run_id=discovered.root_skill_run_id,
        child_skill_run_ids=discovered.child_skill_run_ids,
        validate_child_parent=False,
        invocation_ids=discovered.invocation_ids,
        tool_call_ids=discovered.tool_call_ids,
        attempt_ids=discovered.attempt_ids,
    )
    require_runtime_root_lock(
        session,
        runtime_lock,
        run_id=discovered.run.id,
        turn_id=discovered.turn.id,
        task_id=discovered.run.task_id,
        invocation_ids=discovered.invocation_ids,
        tool_call_ids=discovered.tool_call_ids,
        attempt_ids=discovered.attempt_ids,
    )
    _require_locked_source(runtime_lock, discovered)

    existing_rows = list(
        await session.scalars(
            select(TurnInterrupt)
            .where(TurnInterrupt.run_id == run_id)
            .order_by(TurnInterrupt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    same_semantic = next(
        (row for row in existing_rows if row.semantic_key == semantic_key), None
    )
    if same_semantic is not None:
        if _request_identity(same_semantic) != (
            kind,
            public_message,
            action_label,
            response_schema,
            skill_run_id,
            source_type,
            source_id,
            source_version,
        ):
            raise _conflict(
                "INTERRUPT_SEMANTIC_CONFLICT",
                "This interrupt key is already bound to another request.",
            )
        pending_intent = await _record_pending_work_change(
            session,
            interrupt=same_semantic,
            change="requested",
        )
        return InterruptRequestResult(
            interrupt=same_semantic,
            publish_intents=(pending_intent,),
        )
    if any(row.status == "pending" for row in existing_rows):
        raise _conflict(
            "INTERRUPT_ALREADY_PENDING",
            "This turn is already waiting for an operator response.",
        )

    interrupt = TurnInterrupt(
        org_id=discovered.run.org_id,
        account_id=discovered.thread.account_id,
        thread_id=discovered.thread.id,
        turn_id=discovered.turn.id,
        run_id=discovered.run.id,
        skill_run_id=skill_run_id,
        kind=kind,
        status="pending",
        public_message=public_message,
        action_label=action_label,
        response_schema=dict(response_schema),
        source_type=source_type,
        source_id=source_id,
        source_version=source_version,
        semantic_key=semantic_key,
        version=1,
    )
    session.add(interrupt)
    await session.flush([interrupt])

    paused_status = "waiting_permission" if kind == "approval" else "waiting_user"
    closure = await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=discovered.run.id,
            org_id=discovered.run.org_id,
            account_id=discovered.thread.account_id,
            project_id=discovered.thread.project_id,
            thread_id=discovered.thread.id,
            turn_id=discovered.turn.id,
            task_id=discovered.run.task_id,
            skill_run_id=skill_run_id,
        ),
        status=paused_status,
        message=public_message,
        commit=False,
        prelocked=runtime_lock,
    )
    await append_turn_event(
        session,
        _interrupt_event_scope(discovered, interrupt),
        "turn.interrupt_requested",
        _interrupt_event_payload(interrupt),
        f"interrupt:{interrupt.id}:requested",
    )
    pending_intent = await _record_pending_work_change(
        session,
        interrupt=interrupt,
        change="requested",
    )
    await session.refresh(interrupt)
    return InterruptRequestResult(
        interrupt=interrupt,
        publish_intents=(*closure.publish_intents, pending_intent),
    )


async def resolve_interrupt(
    session: AsyncSession,
    *,
    user: User,
    interrupt_id: int,
    expected_version: int,
    idempotency_key: str,
    resolution: dict,
    prelocked: RuntimeRootLock | None = None,
) -> InterruptResolutionResult:
    """Resolve one pending interrupt and queue its original Run in-place."""

    if not idempotency_key.strip():
        raise ValueError("resolution idempotency key is required")
    canonical_resolution, resolution_hash = _canonical_resolution(resolution)
    with session.no_autoflush:
        discovered_interrupt = await session.get(TurnInterrupt, interrupt_id)
    if discovered_interrupt is None:
        raise _not_found()
    discovered = await _discover_runtime(
        session,
        user=user,
        run_id=discovered_interrupt.run_id,
        kind=discovered_interrupt.kind,
        skill_run_id=discovered_interrupt.skill_run_id,
        source_type=discovered_interrupt.source_type,
        source_id=discovered_interrupt.source_id,
        source_version=discovered_interrupt.source_version,
    )
    runtime_lock = prelocked
    if runtime_lock is None and discovered_interrupt.kind == "approval":
        if discovered.source_tool is None:
            raise _not_found()
        approval_lock = await lock_composite_finish_approval(
            session,
            tool_call=discovered.source_tool,
        )
        runtime_lock = approval_lock.runtime_lock
    if runtime_lock is None:
        runtime_lock = await lock_runtime_root_scope(
            session,
            run_id=discovered.run.id,
            expected_turn_id=discovered.turn.id,
            expected_task_id=discovered.run.task_id,
            root_skill_run_id=discovered.root_skill_run_id,
            child_skill_run_ids=discovered.child_skill_run_ids,
            validate_child_parent=False,
            invocation_ids=discovered.invocation_ids,
            tool_call_ids=discovered.tool_call_ids,
            attempt_ids=discovered.attempt_ids,
        )
    require_runtime_root_lock(
        session,
        runtime_lock,
        run_id=discovered.run.id,
        turn_id=discovered.turn.id,
        task_id=discovered.run.task_id,
        invocation_ids=discovered.invocation_ids,
        tool_call_ids=discovered.tool_call_ids,
        attempt_ids=discovered.attempt_ids,
    )
    _require_locked_source(runtime_lock, discovered)
    interrupt = await session.scalar(
        select(TurnInterrupt)
        .where(TurnInterrupt.id == interrupt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if interrupt is None or not _interrupt_matches_runtime(interrupt, discovered):
        raise _not_found()
    if interrupt.status == "resolved":
        if (
            interrupt.resolution_idempotency_key == idempotency_key
            and interrupt.resolution_hash == resolution_hash
            and dict(interrupt.resolution_payload or {}) == canonical_resolution
        ):
            run = await session.get(AgentRun, interrupt.run_id)
            if run is None:
                raise _not_found()
            pending_intent = await _record_pending_work_change(
                session,
                interrupt=interrupt,
                change="resolved",
            )
            return InterruptResolutionResult(
                interrupt=interrupt,
                run=run,
                dispatch_intent=(
                    InterruptDispatchIntent(run_id=run.id)
                    if run.status == "queued"
                    else None
                ),
                publish_intents=(pending_intent,),
                replay_runtime_events=run.status != "queued",
            )
        raise _conflict(
            "INTERRUPT_ALREADY_RESOLVED",
            "This interrupt was resolved with another decision.",
        )
    if interrupt.status != "pending":
        raise _conflict(
            "INTERRUPT_NOT_PENDING",
            "This interrupt can no longer be resolved.",
        )
    if interrupt.version != expected_version:
        raise _conflict(
            "INTERRUPT_VERSION_CONFLICT",
            "The interrupt changed before this response was submitted.",
        )

    _validate_clarification_resolution(interrupt, canonical_resolution)

    resumed_structured_input: dict | None = None
    resumed_skill: SkillRun | None = None
    if interrupt.kind == "clarification" and interrupt.skill_run_id is not None:
        resumed_skill = await session.get(SkillRun, interrupt.skill_run_id)
        if (
            resumed_skill is not None
            and resumed_skill.skill_code == "wechat_article_production"
            and resumed_skill.run_id == discovered.run.id
            and resumed_skill.turn_id == discovered.turn.id
            and resumed_skill.thread_id == discovered.thread.id
            and resumed_skill.org_id == discovered.run.org_id
        ):
            try:
                resolved_input = resolve_missing_primary_cta(
                    dict(resumed_skill.input_snapshot or {}),
                    canonical_resolution,
                )
            except ValidationError as exc:
                raise _invalid_resolution(
                    "Interrupt response does not complete the required article brief."
                ) from exc
            if resolved_input is not None:
                resumed_structured_input = resolved_input.model_dump(
                    mode="json",
                    exclude_none=True,
                )

    now = datetime.now(UTC)
    claimed = await session.execute(
        update(TurnInterrupt)
        .where(
            TurnInterrupt.id == interrupt.id,
            TurnInterrupt.status == "pending",
            TurnInterrupt.version == expected_version,
        )
        .values(
            status="resolved",
            resolution_payload=canonical_resolution,
            resolution_hash=resolution_hash,
            resolution_idempotency_key=idempotency_key,
            resolved_by_id=user.id,
            resolved_at=now,
            version=expected_version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await session.refresh(interrupt)
        if (
            interrupt.status == "resolved"
            and interrupt.resolution_idempotency_key == idempotency_key
            and interrupt.resolution_hash == resolution_hash
            and dict(interrupt.resolution_payload or {}) == canonical_resolution
        ):
            run = await session.get(AgentRun, interrupt.run_id)
            if run is None:
                raise _not_found()
            pending_intent = await _record_pending_work_change(
                session,
                interrupt=interrupt,
                change="resolved",
            )
            return InterruptResolutionResult(
                interrupt=interrupt,
                run=run,
                dispatch_intent=(
                    InterruptDispatchIntent(run_id=run.id)
                    if run.status == "queued"
                    else None
                ),
                publish_intents=(pending_intent,),
                replay_runtime_events=run.status != "queued",
            )
        raise _conflict(
            "INTERRUPT_ALREADY_RESOLVED",
            "This interrupt was resolved with another decision.",
        )
    await session.refresh(interrupt)
    finish_publish_intents: tuple[RuntimePublishIntent, ...] = ()
    approval_handled = False
    if interrupt.kind == "approval":
        await _apply_approval_resolution(
            session,
            interrupt=interrupt,
            resolution=canonical_resolution,
            user=user,
            now=now,
        )
    await append_turn_event(
        session,
        _interrupt_event_scope(discovered, interrupt),
        "turn.interrupt_resolved",
        _interrupt_event_payload(interrupt),
        f"interrupt:{interrupt.id}:resolved",
    )
    pending_intent = await _record_pending_work_change(
        session,
        interrupt=interrupt,
        change="resolved",
    )
    if (
        interrupt.kind == "approval"
        and discovered.source_tool is not None
        and discovered.task is not None
    ):
        try:
            finish_result = await finalize_skill_finish_approval(
                session,
                tool_call=discovered.source_tool,
                task=discovered.task,
                approved=canonical_resolution.get("approved") is True,
                comment=str(canonical_resolution.get("comment") or ""),
                prelocked=runtime_lock,
            )
        except SkillApprovalConflict as exc:
            raise _conflict("INTERRUPT_APPROVAL_CONFLICT", str(exc)) from exc
        approval_handled = finish_result.handled
        finish_publish_intents = finish_result.publish_intents

    run = await session.get(AgentRun, interrupt.run_id)
    turn = await session.get(ConversationTurn, interrupt.turn_id)
    task = await session.get(BrainTask, run.task_id) if run and run.task_id else None
    if run is None or turn is None:
        raise _not_found()
    dispatch_intent = None
    if not approval_handled:
        run.status = "queued"
        run.phase = "queued"
        run.lease_owner = None
        run.leased_until = None
        run.heartbeat_at = None
        run.next_retry_at = None
        run.cancel_requested_at = None
        run.finished_at = None
        run.error_code = None
        run.error_detail = None
        run.request_payload = {
            **dict(run.request_payload or {}),
            "resume_interrupt": {
                "interrupt_id": interrupt.id,
                "kind": interrupt.kind,
                "resolution": canonical_resolution,
                "resolution_hash": resolution_hash,
            },
            **(
                {"trusted_structured_input": resumed_structured_input}
                if resumed_structured_input is not None
                else {}
            ),
            **(
                {
                    "operation": "resume_permission",
                    "tool_call_id": interrupt.source_id,
                    "approved": canonical_resolution.get("approved") is True,
                }
                if interrupt.kind == "approval"
                else {}
            ),
        }
        if resumed_structured_input is not None and resumed_skill is not None:
            resumed_skill.status = "running"
            resumed_skill.error_code = None
        turn.status = "queued"
        if task is not None:
            task.status = BrainTaskStatus.RUNNING
            task.current_focus = "Resuming after operator input"
        dispatch_intent = InterruptDispatchIntent(run_id=run.id)
        await append_turn_event(
            session,
            _interrupt_event_scope(discovered, interrupt),
            "turn.resuming",
            _interrupt_event_payload(interrupt),
            f"interrupt:{interrupt.id}:resuming",
        )
    await session.flush()
    await session.refresh(interrupt)
    return InterruptResolutionResult(
        interrupt=interrupt,
        run=run,
        dispatch_intent=dispatch_intent,
        publish_intents=(*finish_publish_intents, pending_intent),
    )


async def request_stop(
    session: AsyncSession,
    *,
    user: User,
    run_id: int,
    reason: str | None = None,
) -> InterruptStopResult:
    """Stop one Run and cancel all pending interrupts without committing."""

    discovered = await _discover_runtime(
        session,
        user=user,
        run_id=run_id,
        kind="manual_pause",
        skill_run_id=None,
        source_type=None,
        source_id=None,
        source_version=None,
    )
    runtime_lock = await lock_runtime_root_scope(
        session,
        run_id=discovered.run.id,
        expected_turn_id=discovered.turn.id,
        expected_task_id=discovered.run.task_id,
        root_skill_run_id=discovered.root_skill_run_id,
        child_skill_run_ids=discovered.child_skill_run_ids,
        validate_child_parent=False,
        include_run_revisions=True,
    )
    interrupts = list(
        await session.scalars(
            select(TurnInterrupt)
            .where(TurnInterrupt.run_id == run_id)
            .order_by(TurnInterrupt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    now = datetime.now(UTC)
    pending_work_intents: list[RuntimePublishIntent] = []
    for interrupt in interrupts:
        if interrupt.status == "pending":
            interrupt.status = "cancelled"
            interrupt.version += 1
            await append_turn_event(
                session,
                _interrupt_event_scope(discovered, interrupt),
                "turn.interrupt_cancelled",
                _interrupt_event_payload(interrupt),
                f"interrupt:{interrupt.id}:cancelled",
            )
            pending_work_intents.append(
                await _record_pending_work_change(
                    session,
                    interrupt=interrupt,
                    change="cancelled",
                )
            )
    for skill_id in (
        discovered.root_skill_run_id,
        *discovered.child_skill_run_ids,
    ):
        if skill_id is None:
            continue
        skill = await session.get(SkillRun, skill_id)
        if skill is not None and skill.status not in {
            "completed",
            "blocked",
            "failed",
            "cancelled",
            "stopped",
        }:
            skill.status = "stopped"
            skill.error_code = "RUN_STOPPED"
    discovered.run.cancel_requested_at = now
    closure = await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=discovered.run.id,
            org_id=discovered.run.org_id,
            account_id=discovered.thread.account_id,
            project_id=discovered.thread.project_id,
            thread_id=discovered.thread.id,
            turn_id=discovered.turn.id,
            task_id=discovered.run.task_id,
            skill_run_id=discovered.root_skill_run_id,
        ),
        status="stopped",
        message=(reason or "This task was stopped by the operator.").strip(),
        error_code="RUN_STOPPED",
        commit=False,
        prelocked=runtime_lock,
    )
    return InterruptStopResult(
        run_id=discovered.run.id,
        thread_id=discovered.thread.id,
        turn_id=discovered.turn.id,
        client_message_id=discovered.run.client_message_id,
        publish_intents=(*closure.publish_intents, *pending_work_intents),
    )


async def _record_pending_work_change(
    session: AsyncSession,
    *,
    interrupt: TurnInterrupt,
    change: str,
) -> RuntimePublishIntent:
    """Persist one account-only invalidation envelope for the global listener."""

    raw_key = (
        f"pending-work-interrupt-v1:{interrupt.org_id}:{interrupt.account_id}:"
        f"{interrupt.id}:{change}"
    )
    idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    event = await session.scalar(
        select(Event).where(Event.idempotency_key == idempotency_key)
    )
    if event is None:
        event = Event(
            type="pending_work.updated",
            org_id=interrupt.org_id,
            account_id=interrupt.account_id,
            payload={"account_id": interrupt.account_id},
            idempotency_key=idempotency_key,
        )
        session.add(event)
        await session.flush([event])
    return RuntimePublishIntent(
        event_id=event.id,
        event_type=event.type,
        turn_id=None,
    )


async def _discover_runtime(
    session: AsyncSession,
    *,
    user: User,
    run_id: int,
    kind: str,
    skill_run_id: int | None,
    source_type: str | None,
    source_id: int | None,
    source_version: int | None,
) -> _DiscoveredRuntime:
    """Read every root/source identifier before acquiring the Run-first gate."""

    with session.no_autoflush:
        run = await session.get(AgentRun, run_id)
        if run is None or run.turn_id is None or run.thread_id is None:
            raise _not_found()
        thread = await session.get(ConversationThread, run.thread_id)
        turn = await session.get(ConversationTurn, run.turn_id)
        task = await session.get(BrainTask, run.task_id) if run.task_id is not None else None
        skills = list(
            await session.scalars(
                select(SkillRun)
                .where(SkillRun.run_id == run.id)
                .order_by(SkillRun.id)
            )
        )
        tool = (
            await session.get(AgentToolCall, source_id)
            if source_type == "tool_call" and source_id is not None
            else None
        )
        invocation = (
            await session.get(AgentInvocation, tool.invocation_id)
            if tool is not None and tool.invocation_id is not None
            else None
        )
        attempts = (
            list(
                await session.scalars(
                    select(ToolExecutionAttempt)
                    .where(ToolExecutionAttempt.tool_call_id == tool.id)
                    .order_by(ToolExecutionAttempt.id)
                )
            )
            if tool is not None
            else []
        )

    if (
        run.org_id != user.org_id
        or run.requested_by_id != user.id
        or thread is None
        or turn is None
        or thread.org_id != user.org_id
        or thread.created_by_id != user.id
        or turn.thread_id != thread.id
        or turn.org_id != user.org_id
        or run.thread_id != thread.id
        or run.turn_id != turn.id
        or (task is not None and task.org_id != user.org_id)
    ):
        raise _not_found()
    try:
        await require_account_access(session, user, thread.account_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise _not_found() from exc
        raise
    if kind == "approval":
        if source_type != "tool_call" or tool is None:
            raise _not_found()
        if (
            tool.org_id != run.org_id
            or tool.task_id != run.task_id
            or tool.thread_id != run.thread_id
            or tool.turn_id != run.turn_id
            or tool.skill_run_id != skill_run_id
            or not tool.requires_human_confirmation
        ):
            raise _not_found()
        if invocation is not None and invocation.run_id != run.id:
            raise _not_found()
    elif any(
        value is not None for value in (source_type, source_id, source_version)
    ):
        raise ValueError("only approval interrupts may bind a source object")
    if skill_run_id is not None and not any(row.id == skill_run_id for row in skills):
        raise _not_found()

    root = next(
        (row for row in skills if row.skill_code == "operation_iteration"),
        skills[0] if skills else None,
    )
    root_id = root.id if root is not None else None
    return _DiscoveredRuntime(
        run=run,
        thread=thread,
        turn=turn,
        task=task,
        root_skill_run_id=root_id,
        child_skill_run_ids=tuple(row.id for row in skills if row.id != root_id),
        invocation_ids=((invocation.id,) if invocation is not None else ()),
        tool_call_ids=((tool.id,) if tool is not None else ()),
        attempt_ids=tuple(row.id for row in attempts),
        source_tool=tool,
    )


def _require_locked_source(
    token: RuntimeRootLock,
    discovered: _DiscoveredRuntime,
) -> None:
    if not set(discovered.invocation_ids).issubset(token.invocation_ids):
        raise RuntimeError("interrupt source invocation was not prelocked")
    if not set(discovered.tool_call_ids).issubset(token.tool_call_ids):
        raise RuntimeError("interrupt source tool was not prelocked")
    if not set(discovered.attempt_ids).issubset(token.attempt_ids):
        raise RuntimeError("interrupt source attempts were not prelocked")


def _interrupt_event_scope(
    discovered: _DiscoveredRuntime,
    interrupt: TurnInterrupt,
) -> TurnEventScope:
    return TurnEventScope(
        org_id=discovered.run.org_id,
        account_id=discovered.thread.account_id,
        thread_id=discovered.thread.id,
        turn_id=discovered.turn.id,
        run_id=discovered.run.id,
        skill_run_id=interrupt.skill_run_id,
    )


def _interrupt_event_payload(interrupt: TurnInterrupt) -> dict[str, object]:
    return {
        "interrupt_id": interrupt.id,
        "kind": interrupt.kind,
        "status": interrupt.status,
        "message": interrupt.public_message,
        "action_label": interrupt.action_label,
        "version": interrupt.version,
    }


def _request_identity(row: TurnInterrupt) -> tuple:
    return (
        row.kind,
        row.public_message,
        row.action_label,
        dict(row.response_schema or {}),
        row.skill_run_id,
        row.source_type,
        row.source_id,
        row.source_version,
    )


def _canonical_resolution(value: dict) -> tuple[dict, str]:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    normalized = json.loads(encoded)
    return normalized, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_clarification_resolution(
    interrupt: TurnInterrupt,
    resolution: dict,
) -> None:
    """Validate new object schemas while preserving schema-less legacy pauses."""

    if interrupt.kind != "clarification":
        return
    schema = dict(interrupt.response_schema or {})
    required = schema.get("required")
    properties = schema.get("properties")
    if (
        schema.get("type") != "object"
        or not isinstance(required, list)
        or not required
        or not all(isinstance(field, str) and field for field in required)
        or len(required) != len(set(required))
        or not isinstance(properties, dict)
        or any(field not in properties for field in required)
    ):
        return
    missing = [field for field in required if field not in resolution]
    if missing:
        raise _invalid_resolution(
            "Interrupt response is missing required fields: " + ", ".join(missing)
        )
    if schema.get("additionalProperties") is False:
        unexpected = sorted(set(resolution) - set(properties))
        if unexpected:
            raise _invalid_resolution(
                "Interrupt response contains unexpected fields: " + ", ".join(unexpected)
            )
    for field, value in resolution.items():
        property_schema = properties.get(field)
        if isinstance(property_schema, dict) and not _matches_property_schema(
            value,
            property_schema,
        ):
            raise _invalid_resolution(
                f"Interrupt response field does not match its schema: {field}"
            )


def _matches_property_schema(value: object, schema: dict) -> bool:
    if "const" in schema and value != schema["const"]:
        return False
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        return False
    expected_type = schema.get("type")
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return type(value) is bool
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return expected_type is None


def _interrupt_matches_runtime(
    interrupt: TurnInterrupt,
    discovered: _DiscoveredRuntime,
) -> bool:
    return (
        interrupt.org_id == discovered.run.org_id
        and interrupt.account_id == discovered.thread.account_id
        and interrupt.thread_id == discovered.thread.id
        and interrupt.turn_id == discovered.turn.id
        and interrupt.run_id == discovered.run.id
    )


async def _apply_approval_resolution(
    session: AsyncSession,
    *,
    interrupt: TurnInterrupt,
    resolution: dict,
    user: User,
    now: datetime,
) -> None:
    approved = resolution.get("approved")
    if type(approved) is not bool:
        raise _conflict(
            "INTERRUPT_RESOLUTION_INVALID",
            "Approval responses must include an approved boolean.",
        )
    if interrupt.source_type != "tool_call" or interrupt.source_id is None:
        raise _conflict(
            "INTERRUPT_SOURCE_MISSING",
            "The approval source is no longer available.",
        )
    tool = await session.get(AgentToolCall, interrupt.source_id)
    if tool is None:
        raise _not_found()
    comment = str(resolution.get("comment") or "")
    decision = {
        "approved": approved,
        "comment": comment,
        "reviewed_by": user.id,
        "reviewed_at": now.isoformat(),
    }
    tool.status = "success" if approved else "failed"
    tool.error = None if approved else comment or "Operator rejected this action"
    tool.meta = {**dict(tool.meta or {}), "decision": decision}
    tool.finished_at = now


__all__ = [
    "InterruptDispatchIntent",
    "InterruptRequestResult",
    "InterruptResolutionResult",
    "InterruptStopResult",
    "request_interrupt",
    "request_stop",
    "resolve_interrupt",
]
