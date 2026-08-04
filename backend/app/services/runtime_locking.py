"""Shared Run-root locking protocol for runtime mutations."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ConversationTurn,
    Deliverable,
    RunRevision,
    SkillRun,
    ToolExecutionAttempt,
)

_TOKEN_SEAL = object()
_ADVISORY_NAMESPACE = 0x47454F5A


class RuntimeLockConflict(ValueError):
    """A requested lock set does not belong to one durable Run root."""


class RuntimeRootLock:
    """Unforgeable, session-bound proof of one acquired Run-root lock set."""

    _session_identity: int
    _transaction_identity: int
    run_id: int
    turn_id: int | None
    task_id: int | None
    content_item_id: int | None
    root_skill_run_id: int | None
    child_skill_run_ids: tuple[int, ...]
    run_revision_ids: tuple[int, ...]
    deliverable_ids: tuple[int, ...]
    invocation_ids: tuple[int, ...]
    tool_call_ids: tuple[int, ...]
    attempt_ids: tuple[int, ...]

    __slots__ = (
        "_session_identity",
        "_transaction_identity",
        "run_id",
        "turn_id",
        "task_id",
        "content_item_id",
        "root_skill_run_id",
        "child_skill_run_ids",
        "run_revision_ids",
        "deliverable_ids",
        "invocation_ids",
        "tool_call_ids",
        "attempt_ids",
    )

    def __init__(
        self,
        *,
        _seal: object,
        session_identity: int,
        transaction_identity: int,
        run_id: int,
        turn_id: int | None,
        task_id: int | None,
        content_item_id: int | None,
        root_skill_run_id: int | None,
        child_skill_run_ids: tuple[int, ...],
        run_revision_ids: tuple[int, ...],
        deliverable_ids: tuple[int, ...],
        invocation_ids: tuple[int, ...],
        tool_call_ids: tuple[int, ...],
        attempt_ids: tuple[int, ...],
    ) -> None:
        if _seal is not _TOKEN_SEAL:
            raise TypeError("RuntimeRootLock tokens are created only by the lock helper")
        object.__setattr__(self, "_session_identity", session_identity)
        object.__setattr__(self, "_transaction_identity", transaction_identity)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "turn_id", turn_id)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "content_item_id", content_item_id)
        object.__setattr__(self, "root_skill_run_id", root_skill_run_id)
        object.__setattr__(self, "child_skill_run_ids", child_skill_run_ids)
        object.__setattr__(self, "run_revision_ids", run_revision_ids)
        object.__setattr__(self, "deliverable_ids", deliverable_ids)
        object.__setattr__(self, "invocation_ids", invocation_ids)
        object.__setattr__(self, "tool_call_ids", tool_call_ids)
        object.__setattr__(self, "attempt_ids", attempt_ids)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("RuntimeRootLock is immutable")


def _session_identity(session: AsyncSession) -> int:
    return id(session.sync_session)


def _transaction_identity(session: AsyncSession) -> int:
    transaction = session.get_transaction()
    if transaction is None:
        raise RuntimeLockConflict("runtime lock requires an active transaction")
    return id(transaction.sync_transaction)


async def _advisory_run_gate(session: AsyncSession, run_ids: tuple[int, ...]) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for run_id in run_ids:
        await session.scalar(
            select(func.pg_advisory_xact_lock(_ADVISORY_NAMESPACE, run_id))
        )


async def lock_runtime_run_headers(
    session: AsyncSession,
    *,
    run_ids: tuple[int, ...],
    expected_task_ids: tuple[int, ...] = (),
) -> tuple[AgentRun, ...]:
    """Lock multiple runtime headers by globally sorted row family."""

    run_ids = tuple(sorted(set(run_ids)))
    expected_task_ids = tuple(sorted(set(expected_task_ids)))
    if not run_ids:
        return ()
    await _advisory_run_gate(session, run_ids)
    runs = tuple(await _lock_rows(session, AgentRun, run_ids))
    turn_ids = tuple(sorted({run.turn_id for run in runs if run.turn_id is not None}))
    task_ids = tuple(
        sorted(
            {run.task_id for run in runs if run.task_id is not None}
            | set(expected_task_ids)
        )
    )
    await _lock_rows(session, ConversationTurn, turn_ids)
    await _lock_rows(session, BrainTask, task_ids)
    return runs


async def lock_runtime_root_forest(
    session: AsyncSession,
    *,
    run_ids: tuple[int, ...],
    extra_deliverable_ids: tuple[int, ...] = (),
) -> tuple[RuntimeRootLock, ...]:
    """Lock a multi-Run forest by family, never as per-Run bundles."""

    run_ids = tuple(sorted(set(run_ids)))
    if not run_ids:
        return ()
    with session.no_autoflush:
        discovered_runs = list(
            await session.scalars(
                select(AgentRun).where(AgentRun.id.in_(run_ids)).order_by(AgentRun.id)
            )
        )
        task_ids = tuple(
            sorted({run.task_id for run in discovered_runs if run.task_id is not None})
        )
        tasks = list(
            await session.scalars(
                select(BrainTask).where(BrainTask.id.in_(task_ids)).order_by(BrainTask.id)
            )
        )
        content_ids = tuple(
            sorted({task.content_item_id for task in tasks if task.content_item_id is not None})
        )
        skills = list(
            await session.scalars(
                select(SkillRun).where(SkillRun.run_id.in_(run_ids)).order_by(SkillRun.id)
            )
        )
        root_skill_ids = tuple(
            sorted(
                skill.id
                for skill in skills
                if type(
                    dict(skill.output_snapshot or {}).get("composite_parent_skill_run_id")
                )
                is not int
            )
        )
        child_skill_ids = tuple(
            sorted(skill.id for skill in skills if skill.id not in set(root_skill_ids))
        )
        revision_ids = tuple(
            await session.scalars(
                select(RunRevision.id)
                .where(RunRevision.revision_run_id.in_(run_ids))
                .order_by(RunRevision.id)
            )
        )
        deliverable_ids = tuple(
            sorted(
                set(
                    await session.scalars(
                        select(Deliverable.id)
                        .where(Deliverable.run_id.in_(run_ids))
                        .order_by(Deliverable.id)
                    )
                )
                | set(extra_deliverable_ids)
            )
        )
        invocation_ids = tuple(
            await session.scalars(
                select(AgentInvocation.id)
                .where(AgentInvocation.run_id.in_(run_ids))
                .order_by(AgentInvocation.id)
            )
        )
        tool_ids = tuple(
            await session.scalars(
                select(AgentToolCall.id)
                .where(
                    (AgentToolCall.invocation_id.in_(invocation_ids))
                    | (AgentToolCall.skill_run_id.in_(tuple(skill.id for skill in skills)))
                )
                .order_by(AgentToolCall.id)
            )
        )
        attempt_ids = tuple(
            await session.scalars(
                select(ToolExecutionAttempt.id)
                .where(ToolExecutionAttempt.tool_call_id.in_(tool_ids))
                .order_by(ToolExecutionAttempt.id)
            )
        )
    if len(discovered_runs) != len(run_ids):
        raise RuntimeLockConflict("runtime forest AgentRun set changed")
    runs = await lock_runtime_run_headers(session, run_ids=run_ids)
    await _lock_rows(session, ContentItem, content_ids)
    await _lock_rows(session, SkillRun, root_skill_ids)
    await _lock_rows(session, SkillRun, child_skill_ids)
    await _lock_rows(session, RunRevision, revision_ids)
    await _lock_rows(session, Deliverable, deliverable_ids)
    await _lock_rows(session, AgentInvocation, invocation_ids)
    await _lock_rows(session, AgentToolCall, tool_ids)
    await _lock_rows(session, ToolExecutionAttempt, attempt_ids)
    task_by_id = {task.id: task for task in tasks}
    revision_rows = [await session.get(RunRevision, row_id) for row_id in revision_ids]
    deliverable_rows = [await session.get(Deliverable, row_id) for row_id in deliverable_ids]
    invocation_rows = [await session.get(AgentInvocation, row_id) for row_id in invocation_ids]
    tool_rows = [await session.get(AgentToolCall, row_id) for row_id in tool_ids]
    attempt_rows = [await session.get(ToolExecutionAttempt, row_id) for row_id in attempt_ids]
    tokens: list[RuntimeRootLock] = []
    for run in runs:
        run_skills = [skill for skill in skills if skill.run_id == run.id]
        root_candidates = [
            skill
            for skill in run_skills
            if skill.id in root_skill_ids
        ]
        root = next(
            (skill for skill in root_candidates if skill.skill_code == "operation_iteration"),
            root_candidates[0] if root_candidates else None,
        )
        run_skill_ids = {skill.id for skill in run_skills}
        run_invocation_ids = tuple(
            row.id for row in invocation_rows if row is not None and row.run_id == run.id
        )
        run_tool_ids = tuple(
            row.id
            for row in tool_rows
            if row is not None
            and (
                row.skill_run_id in run_skill_ids
                or row.invocation_id in set(run_invocation_ids)
            )
        )
        run_attempt_ids = tuple(
            row.id
            for row in attempt_rows
            if row is not None and row.tool_call_id in set(run_tool_ids)
        )
        task = task_by_id.get(run.task_id)
        tokens.append(
            RuntimeRootLock(
                _seal=_TOKEN_SEAL,
                session_identity=_session_identity(session),
                transaction_identity=_transaction_identity(session),
                run_id=run.id,
                turn_id=run.turn_id,
                task_id=run.task_id,
                content_item_id=(task.content_item_id if task is not None else None),
                root_skill_run_id=(root.id if root is not None else None),
                child_skill_run_ids=tuple(
                    sorted(run_skill_ids - ({root.id} if root is not None else set()))
                ),
                run_revision_ids=tuple(
                    row.id
                    for row in revision_rows
                    if row is not None and row.revision_run_id == run.id
                ),
                deliverable_ids=tuple(
                    row.id
                    for row in deliverable_rows
                    if row is not None and row.run_id == run.id
                ),
                invocation_ids=run_invocation_ids,
                tool_call_ids=run_tool_ids,
                attempt_ids=run_attempt_ids,
            )
        )
    return tuple(tokens)


async def discover_runtime_skill_lock_ids(
    session: AsyncSession,
    skill_run_id: int | None,
) -> tuple[int | None, tuple[int, ...]]:
    """Read only the persisted parent link needed to order root before child."""

    if skill_run_id is None:
        return None, ()
    with session.no_autoflush:
        skill = await session.get(SkillRun, skill_run_id)
    if skill is None:
        raise RuntimeLockConflict("runtime SkillRun is missing")
    parent_id = dict(skill.output_snapshot or {}).get("composite_parent_skill_run_id")
    if type(parent_id) is int:
        return parent_id, (skill.id,)
    return skill.id, ()


async def lock_runtime_root_scope(
    session: AsyncSession,
    *,
    run_id: int,
    expected_turn_id: int | None = None,
    expected_task_id: int | None = None,
    expected_content_item_id: int | None = None,
    root_skill_run_id: int | None = None,
    child_skill_run_ids: tuple[int, ...] = (),
    validate_child_parent: bool = True,
    include_run_revisions: bool = False,
    deliverable_ids: tuple[int, ...] = (),
    invocation_ids: tuple[int, ...] = (),
    tool_call_ids: tuple[int, ...] = (),
    attempt_ids: tuple[int, ...] = (),
) -> RuntimeRootLock:
    """Lock and validate one complete runtime scope in the global order."""

    child_skill_run_ids = tuple(sorted(set(child_skill_run_ids)))
    deliverable_ids = tuple(sorted(set(deliverable_ids)))
    invocation_ids = tuple(sorted(set(invocation_ids)))
    tool_call_ids = tuple(sorted(set(tool_call_ids)))
    attempt_ids = tuple(sorted(set(attempt_ids)))
    await _advisory_run_gate(session, (run_id,))
    run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise RuntimeLockConflict(f"AgentRun not found: {run_id}")
    turn_id = expected_turn_id if expected_turn_id is not None else run.turn_id
    task_id = expected_task_id if expected_task_id is not None else run.task_id
    if expected_turn_id is not None and run.turn_id != expected_turn_id:
        raise RuntimeLockConflict("runtime Turn does not belong to AgentRun")
    if expected_task_id is not None and run.task_id != expected_task_id:
        raise RuntimeLockConflict("runtime BrainTask does not belong to AgentRun")
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
    if turn_id is not None and (
        turn is None
        or turn.id != run.turn_id
        or turn.thread_id != run.thread_id
        or turn.org_id != run.org_id
    ):
        raise RuntimeLockConflict("runtime Turn lineage mismatch")
    if task_id is not None and (
        task is None or task.id != run.task_id or task.org_id != run.org_id
    ):
        raise RuntimeLockConflict("runtime BrainTask lineage mismatch")
    content_item_id = (
        expected_content_item_id
        if expected_content_item_id is not None
        else (task.content_item_id if task is not None else None)
    )
    if (
        expected_content_item_id is not None
        and task is not None
        and task.content_item_id != expected_content_item_id
    ):
        raise RuntimeLockConflict("runtime ContentItem does not belong to BrainTask")
    if content_item_id is not None:
        locked_content_id = await session.scalar(
            select(ContentItem.id)
            .where(ContentItem.id == content_item_id)
            .with_for_update()
        )
        if locked_content_id is None:
            raise RuntimeLockConflict("runtime ContentItem is missing")

    skill_ids = tuple(
        skill_id
        for skill_id in (root_skill_run_id, *child_skill_run_ids)
        if skill_id is not None
    )
    skills: list[SkillRun] = []
    for skill_id in skill_ids:
        skill = await session.scalar(
            select(SkillRun)
            .where(SkillRun.id == skill_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if skill is None:
            raise RuntimeLockConflict("runtime SkillRun is missing")
        if (
            skill.run_id != run.id
            or skill.turn_id != run.turn_id
            or skill.thread_id != run.thread_id
            or skill.task_id != run.task_id
            or skill.org_id != run.org_id
        ):
            raise RuntimeLockConflict("runtime SkillRun ownership/lineage mismatch")
        skills.append(skill)
    if root_skill_run_id is not None and validate_child_parent:
        for child in skills[1:]:
            parent_id = dict(child.output_snapshot or {}).get(
                "composite_parent_skill_run_id"
            )
            if parent_id != root_skill_run_id:
                raise RuntimeLockConflict("runtime child SkillRun parent mismatch")

    revisions = (
        list(
            await session.scalars(
                select(RunRevision)
                .where(RunRevision.revision_run_id == run.id)
                .order_by(RunRevision.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if include_run_revisions
        else []
    )
    locked_deliverables = await _lock_rows(session, Deliverable, deliverable_ids)
    locked_invocations = await _lock_rows(session, AgentInvocation, invocation_ids)
    locked_tools = await _lock_rows(session, AgentToolCall, tool_call_ids)
    locked_attempts = await _lock_rows(session, ToolExecutionAttempt, attempt_ids)
    for row in locked_deliverables:
        if row.run_id != run.id:
            raise RuntimeLockConflict("runtime Deliverable lineage mismatch")
    for row in locked_invocations:
        if row.run_id != run.id:
            raise RuntimeLockConflict("runtime AgentInvocation lineage mismatch")
    for row in locked_tools:
        if row.task_id != run.task_id or (
            row.skill_run_id is not None
            and row.skill_run_id not in {skill.id for skill in skills}
        ):
            raise RuntimeLockConflict("runtime AgentToolCall lineage mismatch")
    locked_tool_ids = {row.id for row in locked_tools}
    for row in locked_attempts:
        if row.tool_call_id not in locked_tool_ids:
            raise RuntimeLockConflict("runtime ToolExecutionAttempt lineage mismatch")

    return RuntimeRootLock(
        _seal=_TOKEN_SEAL,
        session_identity=_session_identity(session),
        transaction_identity=_transaction_identity(session),
        run_id=run.id,
        turn_id=turn_id,
        task_id=task_id,
        content_item_id=content_item_id,
        root_skill_run_id=root_skill_run_id,
        child_skill_run_ids=child_skill_run_ids,
        run_revision_ids=tuple(row.id for row in revisions),
        deliverable_ids=deliverable_ids,
        invocation_ids=invocation_ids,
        tool_call_ids=tool_call_ids,
        attempt_ids=attempt_ids,
    )


async def _lock_rows(session: AsyncSession, model, row_ids: tuple[int, ...]):
    rows = []
    for row_id in row_ids:
        row = await session.scalar(
            select(model)
            .where(model.id == row_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise RuntimeLockConflict(f"{model.__name__} is missing")
        rows.append(row)
    return rows


def require_runtime_root_lock(
    session: AsyncSession,
    token: RuntimeRootLock,
    *,
    run_id: int,
    turn_id: int | None = None,
    task_id: int | None = None,
    content_item_id: int | None = None,
    skill_run_id: int | None = None,
    deliverable_id: int | None = None,
    tool_call_id: int | None = None,
) -> None:
    """Reject forged, cross-session, or incomplete prelock proofs."""

    if not isinstance(token, RuntimeRootLock) or (
        token._session_identity != _session_identity(session)
    ):
        raise RuntimeLockConflict("runtime lock token does not belong to this session")
    if token._transaction_identity != _transaction_identity(session):
        raise RuntimeLockConflict("runtime lock token belongs to another transaction")
    if token.run_id != run_id:
        raise RuntimeLockConflict("runtime lock token belongs to another AgentRun")
    if turn_id is not None and token.turn_id != turn_id:
        raise RuntimeLockConflict("runtime Turn was not prelocked")
    if task_id is not None and token.task_id != task_id:
        raise RuntimeLockConflict("runtime BrainTask was not prelocked")
    if content_item_id is not None and token.content_item_id != content_item_id:
        raise RuntimeLockConflict("runtime ContentItem was not prelocked")
    locked_skill_ids = {
        token.root_skill_run_id,
        *token.child_skill_run_ids,
    }
    if skill_run_id is not None and skill_run_id not in locked_skill_ids:
        raise RuntimeLockConflict("runtime SkillRun was not prelocked")
    if deliverable_id is not None and deliverable_id not in token.deliverable_ids:
        raise RuntimeLockConflict("runtime Deliverable was not prelocked")
    if tool_call_id is not None and tool_call_id not in token.tool_call_ids:
        raise RuntimeLockConflict("runtime AgentToolCall was not prelocked")


__all__ = [
    "RuntimeLockConflict",
    "RuntimeRootLock",
    "discover_runtime_skill_lock_ids",
    "lock_runtime_root_scope",
    "lock_runtime_root_forest",
    "lock_runtime_run_headers",
    "require_runtime_root_lock",
]
