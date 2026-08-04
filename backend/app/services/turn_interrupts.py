"""Single transactional owner for recoverable human turn interrupts."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import require_account_access
from app.models import (
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    SkillRun,
    ToolExecutionAttempt,
    TurnInterrupt,
    User,
)
from app.services.runtime_locking import RuntimeRootLock, lock_runtime_root_scope
from app.services.runtime_state import (
    RuntimePublishIntent,
    RuntimeStateScope,
    close_runtime_state,
)

_INTERRUPT_KINDS = frozenset({"clarification", "approval", "manual_pause"})


@dataclass(frozen=True)
class InterruptRequestResult:
    interrupt: TurnInterrupt
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


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interrupt not found")


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message},
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
    )
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
        return InterruptRequestResult(interrupt=same_semantic)
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
    return InterruptRequestResult(
        interrupt=interrupt,
        publish_intents=closure.publish_intents,
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
    elif any(value is not None for value in (source_type, source_id)):
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


__all__ = ["InterruptRequestResult", "request_interrupt"]

