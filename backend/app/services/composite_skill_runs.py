"""Durable resume transitions for parent/child SkillRun graphs."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentRun,
    AgentToolCall,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    SkillRun,
    ToolExecutionAttempt,
)
from app.models.enums import BrainTaskStatus, DeliverableStatus
from app.services.runtime_locking import (
    RuntimeRootLock,
    lock_runtime_root_forest,
    lock_runtime_root_scope,
    require_runtime_root_lock,
)
from app.services.runtime_state import (
    RuntimeStateClosure,
    RuntimeStateScope,
    close_runtime_state,
)

_ACTIVE_CHILD_STATUSES = {
    "running",
    "retry_wait",
    "waiting_permission",
    "needs_review",
}


def _parent_id(skill_run: SkillRun) -> int | None:
    value = dict(skill_run.output_snapshot or {}).get("composite_parent_skill_run_id")
    return value if type(value) is int else None


def _interrupt_artifact_ids(parent: SkillRun) -> list[int]:
    report = dict(parent.output_snapshot or {}).get("report")
    interrupt = report.get("interrupt") if isinstance(report, dict) else None
    values = interrupt.get("source_artifact_ids") if isinstance(interrupt, dict) else None
    if not isinstance(values, list) or any(type(value) is not int for value in values):
        return []
    return sorted(set(values))


async def _lock_composite_scope(
    session: AsyncSession,
    *,
    parent_id: int,
    extra_artifact_ids: tuple[int, ...] = (),
    invocation_ids: tuple[int, ...] = (),
    tool_call_ids: tuple[int, ...] = (),
    attempt_ids: tuple[int, ...] = (),
    prelocked: RuntimeRootLock | None = None,
) -> tuple[SkillRun, list[SkillRun], list[Deliverable], RuntimeRootLock]:
    """Lock one composite graph in the protocol's global order.

    Discovery reads are deliberately non-mutating. Every decision is made from
    the rows reloaded after the locks have been acquired.
    """

    with session.no_autoflush:
        discovered = await session.get(SkillRun, parent_id)
    if discovered is None:
        raise ValueError("COMPOSITE_PARENT_SKILL_RUN_MISSING")
    with session.no_autoflush:
        discovered_task = (
            await session.get(BrainTask, discovered.task_id)
            if discovered.task_id is not None
            else None
        )
    if discovered_task is None:
        raise ValueError("COMPOSITE_PARENT_RUNTIME_SCOPE_MISSING")
    with session.no_autoflush:
        child_ids = tuple(
            await session.scalars(
                select(SkillRun.id)
                .where(SkillRun.run_id == discovered.run_id, SkillRun.id != parent_id)
                .order_by(SkillRun.id)
            )
        )
    artifact_ids = tuple(
        sorted(set(_interrupt_artifact_ids(discovered)) | set(extra_artifact_ids))
    )
    if prelocked is None:
        runtime_lock = await lock_runtime_root_scope(
            session,
            run_id=discovered.run_id,
            expected_turn_id=discovered.turn_id,
            expected_task_id=discovered.task_id,
            expected_content_item_id=discovered_task.content_item_id,
            root_skill_run_id=parent_id,
            child_skill_run_ids=child_ids,
            deliverable_ids=artifact_ids,
            invocation_ids=invocation_ids,
            tool_call_ids=tool_call_ids,
            attempt_ids=attempt_ids,
        )
    else:
        require_runtime_root_lock(
            session,
            prelocked,
            run_id=discovered.run_id,
            turn_id=discovered.turn_id,
            task_id=discovered.task_id,
            content_item_id=discovered_task.content_item_id,
            skill_run_id=parent_id,
        )
        for child_id in child_ids:
            require_runtime_root_lock(
                session, prelocked, run_id=discovered.run_id, skill_run_id=child_id
            )
        for artifact_id in artifact_ids:
            require_runtime_root_lock(
                session,
                prelocked,
                run_id=discovered.run_id,
                deliverable_id=artifact_id,
            )
        for tool_call_id in tool_call_ids:
            require_runtime_root_lock(
                session,
                prelocked,
                run_id=discovered.run_id,
                tool_call_id=tool_call_id,
            )
        require_runtime_root_lock(
            session,
            prelocked,
            run_id=discovered.run_id,
            invocation_ids=invocation_ids,
            attempt_ids=attempt_ids,
        )
        runtime_lock = prelocked
    parent = await session.get(SkillRun, parent_id)
    if parent is None or parent.skill_code != "operation_iteration":
        raise ValueError("COMPOSITE_PARENT_SKILL_RUN_MISSING")
    children = [await session.get(SkillRun, child_id) for child_id in child_ids]
    if any(child is None for child in children):
        raise ValueError("COMPOSITE_PARENT_SKILL_SCOPE_CONFLICT")
    for child in children:
        if _parent_id(child) != parent.id:
            raise ValueError("COMPOSITE_PARENT_SKILL_SCOPE_CONFLICT")
        if (
            child.task_id != parent.task_id
            or child.thread_id != parent.thread_id
            or child.turn_id != parent.turn_id
            or child.org_id != parent.org_id
        ):
            raise ValueError("COMPOSITE_PARENT_SKILL_SCOPE_CONFLICT")
    artifacts = [await session.get(Deliverable, artifact_id) for artifact_id in artifact_ids]
    if any(artifact is None for artifact in artifacts):
        raise ValueError("COMPOSITE_CHILD_ARTIFACT_MISSING")
    return parent, children, artifacts, runtime_lock


@dataclass(frozen=True)
class SkillFinishApprovalLock:
    tool_call: AgentToolCall
    runtime_lock: RuntimeRootLock | None


@dataclass(frozen=True)
class CompositeArtifactAcceptanceLock:
    artifact: Deliverable
    runtime_lock: RuntimeRootLock | None


async def lock_composite_artifact_acceptance(
    session: AsyncSession, *, artifact: Deliverable
) -> CompositeArtifactAcceptanceLock:
    """Join artifact acceptance to the same parent-first transition lock."""

    if artifact.skill_run_id is None:
        return CompositeArtifactAcceptanceLock(artifact=artifact, runtime_lock=None)
    child = await session.get(SkillRun, artifact.skill_run_id)
    parent_id = _parent_id(child) if child is not None else None
    if parent_id is None:
        return CompositeArtifactAcceptanceLock(artifact=artifact, runtime_lock=None)
    with session.no_autoflush:
        stream_rows = list(
            await session.scalars(
                select(Deliverable)
                .where(
                    Deliverable.content_item_id == artifact.content_item_id,
                    Deliverable.agent_code == artifact.agent_code,
                    Deliverable.type == artifact.type,
                )
                .order_by(Deliverable.id)
            )
        )
    stream_ids = tuple(row.id for row in stream_rows)
    stream_run_ids = tuple(
        sorted(
            {row.run_id for row in stream_rows if row.run_id is not None}
            | {child.run_id}
        )
    )
    forest_tokens = await lock_runtime_root_forest(
        session,
        run_ids=stream_run_ids,
        extra_deliverable_ids=stream_ids,
    )
    runtime_lock = next(
        (token for token in forest_tokens if token.run_id == child.run_id), None
    )
    if runtime_lock is None:
        raise ValueError("COMPOSITE_PARENT_RUNTIME_SCOPE_MISSING")
    current_run_stream_ids = tuple(
        row.id for row in stream_rows if row.run_id == child.run_id
    )
    _parent, _children, artifacts, runtime_lock = await _lock_composite_scope(
        session,
        parent_id=parent_id,
        extra_artifact_ids=current_run_stream_ids,
        prelocked=runtime_lock,
    )
    locked = await session.get(Deliverable, artifact.id)
    if locked is None:
        raise ValueError("COMPOSITE_CHILD_ARTIFACT_MISSING")
    return CompositeArtifactAcceptanceLock(
        artifact=locked, runtime_lock=runtime_lock
    )


async def lock_composite_finish_approval(
    session: AsyncSession, *, tool_call: AgentToolCall
) -> SkillFinishApprovalLock:
    """Lock every finish approval before its API mutates any ledger row."""

    if tool_call.skill_run_id is None:
        return SkillFinishApprovalLock(tool_call=tool_call, runtime_lock=None)
    with session.no_autoflush:
        skill = await session.get(SkillRun, tool_call.skill_run_id)
    if skill is None or skill.task_id is None:
        raise ValueError("SKILL_APPROVAL_RUNTIME_SCOPE_MISSING")
    artifact_id = dict(tool_call.meta or {}).get("artifact_id")
    deliverable_ids = (artifact_id,) if type(artifact_id) is int else ()
    with session.no_autoflush:
        invocation_ids = (
            (tool_call.invocation_id,)
            if tool_call.invocation_id is not None
            else ()
        )
        attempt_ids = tuple(
            await session.scalars(
                select(ToolExecutionAttempt.id)
                .where(ToolExecutionAttempt.tool_call_id == tool_call.id)
                .order_by(ToolExecutionAttempt.id)
            )
        )
    parent_id = _parent_id(skill)
    if parent_id is not None:
        _parent, _children, _artifacts, runtime_lock = await _lock_composite_scope(
            session,
            parent_id=parent_id,
            extra_artifact_ids=deliverable_ids,
            invocation_ids=invocation_ids,
            tool_call_ids=(tool_call.id,),
            attempt_ids=attempt_ids,
        )
    else:
        with session.no_autoflush:
            task = await session.get(BrainTask, skill.task_id)
        if task is None:
            raise ValueError("SKILL_APPROVAL_RUNTIME_SCOPE_MISSING")
        runtime_lock = await lock_runtime_root_scope(
            session,
            run_id=skill.run_id,
            expected_turn_id=skill.turn_id,
            expected_task_id=skill.task_id,
            expected_content_item_id=task.content_item_id,
            root_skill_run_id=skill.id,
            deliverable_ids=deliverable_ids,
            invocation_ids=invocation_ids,
            tool_call_ids=(tool_call.id,),
            attempt_ids=attempt_ids,
        )
    locked = await session.get(AgentToolCall, tool_call.id)
    if locked is None:
        raise ValueError("COMPOSITE_CHILD_APPROVAL_MISSING")
    return SkillFinishApprovalLock(tool_call=locked, runtime_lock=runtime_lock)


def resolve_composite_recovery_root(skill_runs: list[SkillRun]) -> SkillRun | None:
    """Validate one durable composite tree and return its deterministic root."""

    if not skill_runs:
        return None
    by_id = {item.id: item for item in skill_runs}
    if len(by_id) != len(skill_runs):
        raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:duplicate_node")
    roots = [item for item in skill_runs if _parent_id(item) is None]
    linked = [item for item in skill_runs if _parent_id(item) is not None]
    active = [item for item in skill_runs if item.status in _ACTIVE_CHILD_STATUSES]
    composite_intent = bool(linked) or any(
        item.skill_code == "operation_iteration" for item in skill_runs
    )
    if not linked:
        if composite_intent:
            if len(roots) != 1 or roots[0].skill_code != "operation_iteration":
                raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:multiple_roots")
            return roots[0]
        if len(active) > 1 or (len(roots) > 1 and active):
            raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:multiple_roots")
        return active[0] if active else (roots[0] if len(roots) == 1 else None)
    if len(roots) != 1 or roots[0].skill_code != "operation_iteration":
        raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:root")
    root = roots[0]
    root_scope = (
        root.run_id,
        root.task_id,
        root.thread_id,
        root.turn_id,
        root.org_id,
    )
    active_children: list[SkillRun] = []
    for child in linked:
        parent_id = _parent_id(child)
        if parent_id is None:
            raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:missing_parent")
        parent = by_id.get(parent_id)
        if parent is None:
            raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:missing_parent")
        child_scope = (
            child.run_id,
            child.task_id,
            child.thread_id,
            child.turn_id,
            child.org_id,
        )
        if child_scope != root_scope:
            raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:lineage")
        seen = {child.id}
        cursor = parent
        while True:
            cursor_parent_id = _parent_id(cursor)
            if cursor_parent_id is None:
                break
            if cursor.id in seen:
                raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:cycle")
            seen.add(cursor.id)
            next_parent = by_id.get(cursor_parent_id)
            if next_parent is None:
                raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:missing_parent")
            cursor = next_parent
        if cursor.id != root.id:
            raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:disjoint")
        if child.status in _ACTIVE_CHILD_STATUSES:
            active_children.append(child)
    if len(active_children) > 1:
        raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:multiple_active_branches")
    if root.status not in _ACTIVE_CHILD_STATUSES and active_children:
        raise ValueError("COMPOSITE_RECOVERY_GRAPH_INVALID:terminal_root")
    return root


async def pause_composite_parent_for_artifacts(
    session: AsyncSession,
    *,
    parent_skill_run: SkillRun,
    source_artifact_ids: list[int],
) -> bool:
    """Lock the parent and recheck approvals before a durable pause."""

    parent, _children, artifacts, _runtime_lock = await _lock_composite_scope(
        session,
        parent_id=parent_skill_run.id,
        extra_artifact_ids=tuple(source_artifact_ids),
    )
    approved_ids = {item.id for item in artifacts if item.status == DeliverableStatus.APPROVED}
    return approved_ids != set(source_artifact_ids)


async def resume_composite_parent(
    session: AsyncSession,
    *,
    child_skill_run: SkillRun,
    prelocked: RuntimeRootLock | None = None,
) -> int | None:
    """Queue the persisted parent after a paused child reaches completion."""

    parent_id = _parent_id(child_skill_run)
    if parent_id is None:
        return None
    parent, children, _artifacts, _runtime_lock = await _lock_composite_scope(
        session, parent_id=parent_id, prelocked=prelocked
    )
    if (
        parent is None
        or parent.run_id != child_skill_run.run_id
        or parent.task_id != child_skill_run.task_id
        or parent.thread_id != child_skill_run.thread_id
        or parent.turn_id != child_skill_run.turn_id
        or parent.org_id != child_skill_run.org_id
        or parent.skill_code != "operation_iteration"
    ):
        raise ValueError("COMPOSITE_PARENT_SKILL_SCOPE_CONFLICT")
    locked_child = next((item for item in children if item.id == child_skill_run.id), None)
    if locked_child is None:
        raise ValueError("COMPOSITE_CHILD_SKILL_RUN_MISSING")
    if locked_child.status != "completed":
        return None
    if parent.status != "waiting_permission":
        return None

    run = await session.get(AgentRun, parent.run_id)
    turn = await session.get(ConversationTurn, parent.turn_id)
    task = await session.get(BrainTask, parent.task_id) if parent.task_id is not None else None
    if run is None or turn is None or task is None:
        raise ValueError("COMPOSITE_PARENT_RUNTIME_SCOPE_MISSING")

    parent.status = "running"
    parent.error_code = None
    run.status = "queued"
    run.phase = "queued"
    run.error_code = None
    run.error_detail = None
    run.result_payload = {}
    run.lease_owner = None
    run.leased_until = None
    run.next_retry_at = None
    turn.status = "queued"
    task.status = BrainTaskStatus.RUNNING
    task.progress = min(task.progress, 99)
    task.current_focus = "Resuming the next required child Skill."
    await session.flush()
    return run.id


async def resume_composite_parent_after_artifact_acceptance(
    session: AsyncSession,
    *,
    artifact: Deliverable,
    prelocked: RuntimeRootLock | None = None,
) -> int | None:
    """Complete a review-paused child and queue its exact persisted parent."""

    if artifact.status != DeliverableStatus.APPROVED or artifact.skill_run_id is None:
        return None
    child = await session.get(SkillRun, artifact.skill_run_id)
    if child is None:
        return None
    parent_id = _parent_id(child)
    if parent_id is None:
        return None

    parent, children, artifacts, _runtime_lock = await _lock_composite_scope(
        session,
        parent_id=parent_id,
        extra_artifact_ids=(artifact.id,),
        prelocked=prelocked,
    )
    child = next((item for item in children if item.id == artifact.skill_run_id), None)
    if child is None:
        return None
    if (
        parent.run_id != child.run_id
        or parent.task_id != child.task_id
        or parent.thread_id != child.thread_id
        or parent.turn_id != child.turn_id
        or parent.org_id != child.org_id
    ):
        raise ValueError("COMPOSITE_PARENT_SKILL_SCOPE_CONFLICT")
    source_ids = _interrupt_artifact_ids(parent)
    if artifact.id not in source_ids:
        return None
    approved_ids = {item.id for item in artifacts if item.status == DeliverableStatus.APPROVED}
    if approved_ids != set(source_ids):
        return None
    if child.status == "needs_review":
        child.status = "completed"
        child.error_code = None
        child.output_snapshot = {
            **dict(child.output_snapshot or {}),
            "status": "completed",
            "human_review": {"artifact_id": artifact.id, "approved": True},
        }
    return await resume_composite_parent(session, child_skill_run=child)


async def block_composite_parent_from_child(
    session: AsyncSession,
    *,
    child_skill_run: SkillRun,
    error_code: str,
    prelocked: RuntimeRootLock | None = None,
) -> RuntimeStateClosure | None:
    """Fail closed when a required nested child is rejected."""

    parent_id = dict(child_skill_run.output_snapshot or {}).get(
        "composite_parent_skill_run_id"
    )
    if type(parent_id) is not int:
        return None
    parent, children, _artifacts, _runtime_lock = await _lock_composite_scope(
        session,
        parent_id=parent_id,
        prelocked=prelocked,
    )
    locked_child = next(
        (item for item in children if item.id == child_skill_run.id), None
    )
    if locked_child is None:
        raise ValueError("COMPOSITE_CHILD_SKILL_RUN_MISSING")
    child_skill_run = locked_child
    run = await session.get(AgentRun, child_skill_run.run_id)
    turn = await session.get(ConversationTurn, child_skill_run.turn_id)
    thread = await session.get(ConversationThread, child_skill_run.thread_id)
    task = (
        await session.get(BrainTask, child_skill_run.task_id)
        if child_skill_run.task_id is not None
        else None
    )
    if (
        parent is None
        or run is None
        or turn is None
        or thread is None
        or task is None
        or parent.run_id != child_skill_run.run_id
        or parent.task_id != child_skill_run.task_id
        or parent.thread_id != child_skill_run.thread_id
        or parent.turn_id != child_skill_run.turn_id
        or parent.org_id != child_skill_run.org_id
    ):
        raise ValueError("COMPOSITE_PARENT_SKILL_SCOPE_CONFLICT")
    output = dict(parent.output_snapshot or {})
    report = output.get("report")
    if isinstance(report, dict):
        for node in report.get("child_skill_graph", []):
            if (
                isinstance(node, dict)
                and node.get("skill_code") == child_skill_run.skill_code
            ):
                node["status"] = "blocked"
                node["error_code"] = error_code
                node["terminal_reason"] = "required_child_rejected"
        report["required_children_completed"] = False
        report["interrupt"] = {
            "kind": "required_child_rejected",
            "skill_code": child_skill_run.skill_code,
        }
    response = "A required child Skill was rejected; the iteration stopped."
    output.update(
        {
            "status": "blocked",
            "error_code": error_code,
            "response": response,
        }
    )
    return await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            org_id=run.org_id,
            thread_id=turn.thread_id,
            turn_id=turn.id,
            skill_run_id=parent.id,
            task_id=task.id,
            account_id=thread.account_id,
            project_id=thread.project_id,
            content_item_id=task.content_item_id,
            skill_output_snapshot=output,
        ),
        status="blocked",
        message=response,
        error_code=error_code,
        commit=False,
        prelocked=_runtime_lock,
    )


__all__ = [
    "block_composite_parent_from_child",
    "lock_composite_artifact_acceptance",
    "lock_composite_finish_approval",
    "pause_composite_parent_for_artifacts",
    "resolve_composite_recovery_root",
    "resume_composite_parent",
    "resume_composite_parent_after_artifact_acceptance",
]
