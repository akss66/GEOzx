"""Durable resume transitions for parent/child SkillRun graphs."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentRun,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    SkillRun,
)
from app.models.enums import BrainTaskStatus, DeliverableStatus
from app.services.runtime_state import RuntimeStateScope, close_runtime_state


async def resume_composite_parent(
    session: AsyncSession,
    *,
    child_skill_run: SkillRun,
) -> int | None:
    """Queue the persisted parent after a paused child reaches completion."""

    parent_id = dict(child_skill_run.output_snapshot or {}).get(
        "composite_parent_skill_run_id"
    )
    if type(parent_id) is not int:
        return None
    parent = await session.scalar(
        select(SkillRun).where(SkillRun.id == parent_id).with_for_update()
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
    if child_skill_run.status != "completed":
        return None
    if parent.status != "waiting_permission":
        return None

    run = await session.scalar(
        select(AgentRun).where(AgentRun.id == parent.run_id).with_for_update()
    )
    turn = await session.scalar(
        select(ConversationTurn)
        .where(ConversationTurn.id == parent.turn_id)
        .with_for_update()
    )
    task = (
        await session.scalar(
            select(BrainTask).where(BrainTask.id == parent.task_id).with_for_update()
        )
        if parent.task_id is not None
        else None
    )
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
) -> int | None:
    """Complete a review-paused child and queue its exact persisted parent."""

    if artifact.status != DeliverableStatus.APPROVED or artifact.skill_run_id is None:
        return None
    child = await session.scalar(
        select(SkillRun).where(SkillRun.id == artifact.skill_run_id).with_for_update()
    )
    if child is None:
        return None
    parent_id = dict(child.output_snapshot or {}).get("composite_parent_skill_run_id")
    if type(parent_id) is not int:
        return None

    parent = await session.get(SkillRun, parent_id)
    if parent is None:
        raise ValueError("COMPOSITE_PARENT_SKILL_RUN_MISSING")
    report = dict(parent.output_snapshot or {}).get("report")
    interrupt = report.get("interrupt") if isinstance(report, dict) else None
    source_ids = (
        interrupt.get("source_artifact_ids") if isinstance(interrupt, dict) else None
    )
    if not isinstance(source_ids, list) or artifact.id not in source_ids:
        return None
    approved_ids = set(
        await session.scalars(
            select(Deliverable.id).where(
                Deliverable.id.in_(source_ids),
                Deliverable.status == DeliverableStatus.APPROVED,
            )
        )
    )
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
) -> None:
    """Fail closed when a required nested child is rejected."""

    parent_id = dict(child_skill_run.output_snapshot or {}).get(
        "composite_parent_skill_run_id"
    )
    if type(parent_id) is not int:
        return
    parent = await session.get(SkillRun, parent_id)
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
    await close_runtime_state(
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
    )


__all__ = [
    "block_composite_parent_from_child",
    "resume_composite_parent",
    "resume_composite_parent_after_artifact_acceptance",
]
