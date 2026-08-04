"""Shared Run-root locking protocol for runtime mutations."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

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
    _transaction_identity: object
    run_id: int
    turn_id: int | None
    task_id: int | None
    content_item_id: int | None
    content_item_ids: tuple[int, ...]
    root_skill_run_id: int | None
    child_skill_run_ids: tuple[int, ...]
    run_revision_ids: tuple[int, ...]
    deliverable_ids: tuple[int, ...]
    invocation_ids: tuple[int, ...]
    tool_call_ids: tuple[int, ...]
    attempt_ids: tuple[int, ...]
    _pending_object_ids_at_acquire: frozenset[int]

    __slots__ = (
        "_session_identity",
        "_transaction_identity",
        "run_id",
        "turn_id",
        "task_id",
        "content_item_id",
        "content_item_ids",
        "root_skill_run_id",
        "child_skill_run_ids",
        "run_revision_ids",
        "deliverable_ids",
        "invocation_ids",
        "tool_call_ids",
        "attempt_ids",
        "_pending_object_ids_at_acquire",
    )

    def __init__(
        self,
        *,
        _seal: object,
        session_identity: int,
        transaction_identity: object,
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
        content_item_ids: tuple[int, ...] = (),
        pending_object_ids_at_acquire: frozenset[int] = frozenset(),
    ) -> None:
        if _seal is not _TOKEN_SEAL:
            raise TypeError("RuntimeRootLock tokens are created only by the lock helper")
        object.__setattr__(self, "_session_identity", session_identity)
        object.__setattr__(self, "_transaction_identity", transaction_identity)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "turn_id", turn_id)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "content_item_id", content_item_id)
        canonical_content_ids = (
            {content_item_id} if content_item_id is not None else set()
        )
        object.__setattr__(
            self,
            "content_item_ids",
            tuple(sorted(set(content_item_ids) | canonical_content_ids)),
        )
        object.__setattr__(self, "root_skill_run_id", root_skill_run_id)
        object.__setattr__(self, "child_skill_run_ids", child_skill_run_ids)
        object.__setattr__(self, "run_revision_ids", run_revision_ids)
        object.__setattr__(self, "deliverable_ids", deliverable_ids)
        object.__setattr__(self, "invocation_ids", invocation_ids)
        object.__setattr__(self, "tool_call_ids", tool_call_ids)
        object.__setattr__(self, "attempt_ids", attempt_ids)
        object.__setattr__(
            self, "_pending_object_ids_at_acquire", pending_object_ids_at_acquire
        )

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("RuntimeRootLock is immutable")


class RuntimeForestLock(Sequence[RuntimeRootLock]):
    """Session-bound proof for Run tokens plus explicitly allowed runless rows."""

    _session_identity: int
    _transaction_identity: object
    run_tokens: tuple[RuntimeRootLock, ...]
    extra_content_item_ids: tuple[int, ...]
    extra_deliverable_ids: tuple[int, ...]

    __slots__ = (
        "_session_identity",
        "_transaction_identity",
        "run_tokens",
        "extra_content_item_ids",
        "extra_deliverable_ids",
    )

    def __init__(
        self,
        *,
        _seal: object,
        session_identity: int,
        transaction_identity: object,
        run_tokens: tuple[RuntimeRootLock, ...],
        extra_content_item_ids: tuple[int, ...] = (),
        extra_deliverable_ids: tuple[int, ...] = (),
    ) -> None:
        if _seal is not _TOKEN_SEAL:
            raise TypeError("RuntimeForestLock proofs are created only by the lock helper")
        object.__setattr__(self, "_session_identity", session_identity)
        object.__setattr__(self, "_transaction_identity", transaction_identity)
        object.__setattr__(self, "run_tokens", run_tokens)
        object.__setattr__(
            self,
            "extra_content_item_ids",
            tuple(sorted(set(extra_content_item_ids))),
        )
        object.__setattr__(
            self,
            "extra_deliverable_ids",
            tuple(sorted(set(extra_deliverable_ids))),
        )

    def __len__(self) -> int:
        return len(self.run_tokens)

    def __getitem__(self, index):
        return self.run_tokens[index]

    def __iter__(self) -> Iterator[RuntimeRootLock]:
        return iter(self.run_tokens)

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("RuntimeForestLock is immutable")


def _session_identity(session: AsyncSession) -> int:
    return id(session.sync_session)


def _transaction_identity(session: AsyncSession) -> object:
    transaction = session.get_transaction()
    if transaction is None:
        raise RuntimeLockConflict("runtime lock requires an active transaction")
    return transaction.sync_transaction


def _pending_object_ids(session: AsyncSession) -> frozenset[int]:
    return frozenset(id(row) for row in session.new)


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
    allow_runless_extras: bool = False,
) -> RuntimeForestLock:
    """Lock a multi-Run forest by family, never as per-Run bundles."""

    requested_run_ids = tuple(sorted(set(run_ids)))
    extra_deliverable_ids = tuple(sorted(set(extra_deliverable_ids)))
    if not requested_run_ids and not extra_deliverable_ids:
        return RuntimeForestLock(
            _seal=_TOKEN_SEAL,
            session_identity=_session_identity(session),
            transaction_identity=_transaction_identity(session),
            run_tokens=(),
        )
    with session.no_autoflush:
        run_id_set = set(requested_run_ids)
        revisions: list[RunRevision] = []
        while True:
            revisions = list(
                await session.scalars(
                    select(RunRevision)
                    .where(
                        (RunRevision.source_run_id.in_(run_id_set))
                        | (RunRevision.revision_run_id.in_(run_id_set))
                    )
                    .order_by(RunRevision.id)
                )
            )
            expanded = run_id_set | {
                endpoint
                for row in revisions
                for endpoint in (row.source_run_id, row.revision_run_id)
            }
            if expanded == run_id_set:
                break
            run_id_set = expanded
        run_ids = tuple(sorted(run_id_set))
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
        task_content_ids = tuple(
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
        revision_ids = tuple(row.id for row in revisions)
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
        discovered_deliverables = list(
            await session.scalars(
                select(Deliverable)
                .where(Deliverable.id.in_(deliverable_ids))
                .order_by(Deliverable.id)
            )
        )
        content_ids = tuple(
            sorted(
                set(task_content_ids)
                | {row.content_item_id for row in discovered_deliverables}
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
    deliverable_rows = await _lock_rows(session, Deliverable, deliverable_ids)
    await _lock_rows(session, AgentInvocation, invocation_ids)
    await _lock_rows(session, AgentToolCall, tool_ids)
    await _lock_rows(session, ToolExecutionAttempt, attempt_ids)
    task_by_id = {task.id: task for task in tasks}
    revision_rows = [await session.get(RunRevision, row_id) for row_id in revision_ids]
    invocation_rows = [await session.get(AgentInvocation, row_id) for row_id in invocation_ids]
    tool_rows = [await session.get(AgentToolCall, row_id) for row_id in tool_ids]
    attempt_rows = [await session.get(ToolExecutionAttempt, row_id) for row_id in attempt_ids]
    if len(discovered_deliverables) != len(deliverable_ids) or any(
        (row.run_id is not None and row.run_id not in set(run_ids))
        for row in deliverable_rows
    ):
        raise RuntimeLockConflict("runtime Deliverable lineage mismatch")
    runless_deliverables = tuple(
        row for row in deliverable_rows if row.run_id is None
    )
    if runless_deliverables and not allow_runless_extras:
        raise RuntimeLockConflict("runtime Deliverable lineage mismatch")
    rebuilt_content_ids = {
        task.content_item_id
        for task in tasks
        if task.content_item_id is not None
    } | {row.content_item_id for row in deliverable_rows}
    if rebuilt_content_ids != set(content_ids):
        raise RuntimeLockConflict("runtime ContentItem set changed")
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
        task = task_by_id.get(run.task_id) if run.task_id is not None else None
        canonical_content_ids = (
            {task.content_item_id}
            if task is not None and task.content_item_id is not None
            else set()
        )
        run_content_ids = tuple(
            sorted(
                canonical_content_ids
                | {
                    row.content_item_id
                    for row in deliverable_rows
                    if row.run_id == run.id
                }
            )
        )
        tokens.append(
            RuntimeRootLock(
                _seal=_TOKEN_SEAL,
                session_identity=_session_identity(session),
                transaction_identity=_transaction_identity(session),
                run_id=run.id,
                turn_id=run.turn_id,
                task_id=run.task_id,
                content_item_id=(task.content_item_id if task is not None else None),
                content_item_ids=run_content_ids,
                root_skill_run_id=(root.id if root is not None else None),
                child_skill_run_ids=tuple(
                    sorted(run_skill_ids - ({root.id} if root is not None else set()))
                ),
                run_revision_ids=tuple(
                    row.id
                    for row in revision_rows
                    if row is not None
                    and run.id in {row.source_run_id, row.revision_run_id}
                ),
                deliverable_ids=tuple(
                    row.id
                    for row in deliverable_rows
                    if row is not None and row.run_id == run.id
                ),
                invocation_ids=run_invocation_ids,
                tool_call_ids=run_tool_ids,
            attempt_ids=run_attempt_ids,
            pending_object_ids_at_acquire=_pending_object_ids(session),
            )
        )
    return RuntimeForestLock(
        _seal=_TOKEN_SEAL,
        session_identity=_session_identity(session),
        transaction_identity=_transaction_identity(session),
        run_tokens=tuple(tokens),
        extra_content_item_ids=tuple(
            row.content_item_id for row in runless_deliverables
        ),
        extra_deliverable_ids=tuple(row.id for row in runless_deliverables),
    )


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
    transition_task_id: int | None = None,
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
    current_task_id = run.task_id
    if expected_turn_id is not None and run.turn_id != expected_turn_id:
        raise RuntimeLockConflict("runtime Turn does not belong to AgentRun")
    if expected_task_id is not None and current_task_id != expected_task_id:
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
    task_id = transition_task_id if transition_task_id is not None else current_task_id
    task_ids = tuple(
        sorted(
            task_id
            for task_id in {current_task_id, transition_task_id}
            if task_id is not None
        )
    )
    locked_tasks = await _lock_rows(session, BrainTask, task_ids)
    task_by_id = {row.id: row for row in locked_tasks}
    current_task = task_by_id.get(current_task_id)
    task = task_by_id.get(task_id)
    if turn_id is not None and (
        turn is None
        or turn.id != run.turn_id
        or turn.thread_id != run.thread_id
        or turn.org_id != run.org_id
    ):
        raise RuntimeLockConflict("runtime Turn lineage mismatch")
    if current_task_id is not None and (
        current_task is None or current_task.org_id != run.org_id
    ):
        raise RuntimeLockConflict("runtime BrainTask lineage mismatch")
    if task_id is not None and (task is None or task.org_id != run.org_id):
        raise RuntimeLockConflict("runtime target BrainTask lineage mismatch")
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
    discovered_deliverables = list(
        await session.scalars(
            select(Deliverable)
            .where(Deliverable.id.in_(deliverable_ids))
            .order_by(Deliverable.id)
        )
    )
    discovered_deliverable_content_ids = {
        row.content_item_id for row in discovered_deliverables
    }
    content_item_ids = tuple(
        sorted(
            {
                row.content_item_id
                for row in locked_tasks
                if row.content_item_id is not None
            }
            | {row.content_item_id for row in discovered_deliverables}
        )
    )
    await _lock_rows(session, ContentItem, content_item_ids)

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
    if transition_task_id is not None and transition_task_id != current_task_id and (
        skills
        or revisions
        or locked_deliverables
        or locked_invocations
        or locked_tools
        or locked_attempts
    ):
        raise RuntimeLockConflict("runtime cannot rebind existing runtime family")
    if len(discovered_deliverables) != len(deliverable_ids):
        raise RuntimeLockConflict("runtime Deliverable set changed")
    if {
        row.content_item_id for row in locked_deliverables
    } != discovered_deliverable_content_ids:
        raise RuntimeLockConflict("runtime Deliverable ContentItem set changed")
    for row in locked_deliverables:
        if row.run_id != run.id:
            raise RuntimeLockConflict("runtime Deliverable lineage mismatch")
    for row in locked_invocations:
        if row.run_id != run.id:
            raise RuntimeLockConflict("runtime AgentInvocation lineage mismatch")
    locked_skill_ids = {skill.id for skill in skills}
    locked_invocation_ids = {row.id for row in locked_invocations}
    for row in locked_tools:
        through_skill = (
            row.skill_run_id is not None and row.skill_run_id in locked_skill_ids
        )
        through_invocation = (
            row.skill_run_id is None
            and row.invocation_id is not None
            and row.invocation_id in locked_invocation_ids
        )
        if row.task_id != run.task_id or not (through_skill or through_invocation):
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
        content_item_ids=content_item_ids,
        root_skill_run_id=root_skill_run_id,
        child_skill_run_ids=child_skill_run_ids,
        run_revision_ids=tuple(row.id for row in revisions),
        deliverable_ids=deliverable_ids,
        invocation_ids=invocation_ids,
        tool_call_ids=tool_call_ids,
        attempt_ids=attempt_ids,
        pending_object_ids_at_acquire=_pending_object_ids(session),
    )


async def extend_runtime_root_lock(
    session: AsyncSession,
    token: RuntimeRootLock,
    *,
    task: BrainTask,
    content: ContentItem | None,
    skill_run: SkillRun,
    expected_content_account_id: int | None = None,
) -> RuntimeRootLock:
    """Flush and attest rows added only after the Run gate was acquired."""

    require_runtime_root_lock(
        session,
        token,
        run_id=token.run_id,
        turn_id=token.turn_id,
    )
    def require_new(row: object, label: str) -> None:
        if (
            row not in session.new
            or id(row) in token._pending_object_ids_at_acquire
        ):
            raise RuntimeLockConflict(
                f"runtime {label} must be inserted in the current transaction "
                "after root lock acquisition"
            )

    new_task = task.id is None or token.task_id != task.id
    new_content = (
        content is not None
        and (content.id is None or token.content_item_id != content.id)
    )
    locked_skill_ids = {
        token.root_skill_run_id,
        *token.child_skill_run_ids,
    }
    new_skill = skill_run.id is None or skill_run.id not in locked_skill_ids
    if new_task:
        require_new(task, "BrainTask")
    if new_content:
        if content is None:
            raise RuntimeLockConflict("runtime inserted ContentItem is missing")
        require_new(content, "ContentItem")
    if new_skill:
        require_new(skill_run, "SkillRun")

    run = await session.get(AgentRun, token.run_id)
    if run is None or run.turn_id != token.turn_id or (
        task.org_id != run.org_id
        or skill_run.run_id != run.id
        or skill_run.turn_id != run.turn_id
        or skill_run.thread_id != run.thread_id
        or skill_run.org_id != run.org_id
    ):
        raise RuntimeLockConflict("runtime inserted row lineage mismatch")

    if new_content:
        assert content is not None
        if (
            expected_content_account_id is not None
            and content.account_id != expected_content_account_id
        ):
            raise RuntimeLockConflict("runtime inserted ContentItem account mismatch")
        await session.flush([content])
    content_item_id = content.id if content is not None else None
    if new_task:
        task.content_item_id = content_item_id
        await session.flush([task])
    if task.content_item_id != content_item_id:
        raise RuntimeLockConflict("runtime inserted BrainTask content mismatch")
    run.task_id = task.id
    skill_run.task_id = task.id
    await session.flush()
    if (
        run.task_id != task.id
        or skill_run.task_id != task.id
        or skill_run.id is None
    ):
        raise RuntimeLockConflict("runtime inserted row lineage mismatch")

    parent_id = dict(skill_run.output_snapshot or {}).get(
        "composite_parent_skill_run_id"
    )
    if type(parent_id) is int:
        if parent_id not in locked_skill_ids:
            raise RuntimeLockConflict("runtime inserted child SkillRun parent mismatch")
        root_skill_run_id = token.root_skill_run_id
        child_skill_run_ids = tuple(
            sorted(set(token.child_skill_run_ids) | {skill_run.id})
        )
    else:
        if token.root_skill_run_id not in {None, skill_run.id}:
            raise RuntimeLockConflict("runtime inserted root SkillRun conflicts with root")
        root_skill_run_id = skill_run.id
        child_skill_run_ids = token.child_skill_run_ids

    return RuntimeRootLock(
        _seal=_TOKEN_SEAL,
        session_identity=_session_identity(session),
        transaction_identity=_transaction_identity(session),
        run_id=run.id,
        turn_id=run.turn_id,
        task_id=task.id,
        content_item_id=content_item_id,
        content_item_ids=tuple(
            sorted(
                set(token.content_item_ids)
                | ({content_item_id} if content_item_id is not None else set())
            )
        ),
        root_skill_run_id=root_skill_run_id,
        child_skill_run_ids=child_skill_run_ids,
        run_revision_ids=token.run_revision_ids,
        deliverable_ids=token.deliverable_ids,
        invocation_ids=token.invocation_ids,
        tool_call_ids=token.tool_call_ids,
        attempt_ids=token.attempt_ids,
        pending_object_ids_at_acquire=_pending_object_ids(session),
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
    content_item_ids: tuple[int, ...] = (),
    skill_run_id: int | None = None,
    deliverable_id: int | None = None,
    tool_call_id: int | None = None,
    run_revision_ids: tuple[int, ...] = (),
    invocation_ids: tuple[int, ...] = (),
    tool_call_ids: tuple[int, ...] = (),
    attempt_ids: tuple[int, ...] = (),
) -> None:
    """Reject forged, cross-session, or incomplete prelock proofs."""

    if not isinstance(token, RuntimeRootLock) or (
        token._session_identity != _session_identity(session)
    ):
        raise RuntimeLockConflict("runtime lock token does not belong to this session")
    if token._transaction_identity is not _transaction_identity(session):
        raise RuntimeLockConflict("runtime lock token belongs to another transaction")
    if token.run_id != run_id:
        raise RuntimeLockConflict("runtime lock token belongs to another AgentRun")
    if turn_id is not None and token.turn_id != turn_id:
        raise RuntimeLockConflict("runtime Turn was not prelocked")
    if task_id is not None and token.task_id != task_id:
        raise RuntimeLockConflict("runtime BrainTask was not prelocked")
    if content_item_id is not None and token.content_item_id != content_item_id:
        raise RuntimeLockConflict("runtime ContentItem was not prelocked")
    if not set(content_item_ids).issubset(token.content_item_ids):
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
    required_sets = (
        (run_revision_ids, token.run_revision_ids, "RunRevision"),
        (invocation_ids, token.invocation_ids, "AgentInvocation"),
        (tool_call_ids, token.tool_call_ids, "AgentToolCall"),
        (attempt_ids, token.attempt_ids, "ToolExecutionAttempt"),
    )
    for required, locked, label in required_sets:
        if not set(required).issubset(locked):
            raise RuntimeLockConflict(f"runtime {label} was not prelocked")


def require_runtime_forest_lock(
    session: AsyncSession,
    proof: RuntimeForestLock,
    *,
    run_ids: tuple[int, ...] = (),
    extra_content_item_ids: tuple[int, ...] = (),
    extra_deliverable_ids: tuple[int, ...] = (),
) -> None:
    """Reject forged, cross-transaction, or incomplete forest proofs."""

    if not isinstance(proof, RuntimeForestLock) or (
        proof._session_identity != _session_identity(session)
    ):
        raise RuntimeLockConflict("runtime forest proof does not belong to this session")
    if proof._transaction_identity is not _transaction_identity(session):
        raise RuntimeLockConflict("runtime forest proof belongs to another transaction")
    locked_run_ids = {token.run_id for token in proof.run_tokens}
    if not set(run_ids).issubset(locked_run_ids):
        raise RuntimeLockConflict("runtime forest AgentRun was not prelocked")
    if not set(extra_content_item_ids).issubset(proof.extra_content_item_ids):
        raise RuntimeLockConflict("runtime extra ContentItem was not prelocked")
    if not set(extra_deliverable_ids).issubset(proof.extra_deliverable_ids):
        raise RuntimeLockConflict("runtime extra Deliverable was not prelocked")


__all__ = [
    "RuntimeForestLock",
    "RuntimeLockConflict",
    "RuntimeRootLock",
    "discover_runtime_skill_lock_ids",
    "extend_runtime_root_lock",
    "lock_runtime_root_scope",
    "lock_runtime_root_forest",
    "lock_runtime_run_headers",
    "require_runtime_forest_lock",
    "require_runtime_root_lock",
]
