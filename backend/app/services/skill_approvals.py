"""Approval convergence for V3 Skills that pause before completion."""

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
)
from app.models.enums import DeliverableStatus
from app.services.composite_skill_runs import (
    block_composite_parent_from_child,
    resume_composite_parent,
)
from app.services.runtime_state import (
    RuntimePublishIntent,
    RuntimeStateScope,
    close_runtime_state,
)


class SkillApprovalConflict(RuntimeError):
    """Persisted Skill approval provenance cannot be safely reconciled."""


@dataclass(frozen=True)
class SkillFinishApprovalResult:
    handled: bool
    publish_intents: tuple[RuntimePublishIntent, ...] = ()


async def finalize_skill_finish_approval(
    session: AsyncSession,
    *,
    tool_call: AgentToolCall,
    task: BrainTask,
    approved: bool,
    comment: str | None,
) -> SkillFinishApprovalResult:
    """Close a typed `before_finish` Skill without owning an outer commit."""

    if tool_call.skill_run_id is None:
        return SkillFinishApprovalResult(handled=False)
    meta = dict(tool_call.meta or {})
    if meta.get("approval_stage") != "before_finish":
        return SkillFinishApprovalResult(handled=False)

    skill_run = await session.get(SkillRun, tool_call.skill_run_id)
    if skill_run is None:
        raise SkillApprovalConflict("SKILL_APPROVAL_RUN_MISSING")
    run = await session.get(AgentRun, skill_run.run_id)
    turn = await session.get(ConversationTurn, skill_run.turn_id)
    thread = await session.get(ConversationThread, skill_run.thread_id)
    if (
        run is None
        or turn is None
        or thread is None
        or skill_run.task_id != task.id
        or run.task_id != task.id
        or run.turn_id != turn.id
        or run.thread_id != thread.id
        or turn.thread_id != thread.id
        or tool_call.task_id != task.id
        or tool_call.thread_id != thread.id
        or tool_call.turn_id != turn.id
        or tool_call.org_id != task.org_id
        or skill_run.org_id != task.org_id
        or run.org_id != task.org_id
        or turn.org_id != task.org_id
        or thread.org_id != task.org_id
    ):
        raise SkillApprovalConflict("SKILL_APPROVAL_SCOPE_CONFLICT")
    if skill_run.status != "waiting_permission" or run.status != "waiting_permission":
        raise SkillApprovalConflict("SKILL_APPROVAL_STATE_CONFLICT")

    artifact_id = meta.get("artifact_id")
    deliverable = (
        await session.scalar(
            select(Deliverable).where(
                Deliverable.id == artifact_id,
                Deliverable.skill_run_id == skill_run.id,
                Deliverable.run_id == run.id,
                Deliverable.thread_id == thread.id,
                Deliverable.turn_id == turn.id,
            )
        )
        if isinstance(artifact_id, int)
        else None
    )
    if deliverable is None:
        raise SkillApprovalConflict("SKILL_APPROVAL_ARTIFACT_MISSING")

    output = dict(skill_run.output_snapshot or {})
    next_status = "completed" if approved else "blocked"
    response = (
        "发布准备已确认采用，本次任务已完成。"
        if approved
        else f"发布准备未被采用，本次任务已停止。{comment or ''}".strip()
    )
    output.update(
        {
            "status": next_status,
            "response": response,
            "approval": {
                "approved": approved,
                "comment": comment or "",
                "tool_call_id": tool_call.id,
            },
        }
    )
    deliverable.status = (
        DeliverableStatus.APPROVED if approved else DeliverableStatus.REJECTED
    )
    nested_parent_id = dict(skill_run.output_snapshot or {}).get(
        "composite_parent_skill_run_id"
    )
    nested_child = type(nested_parent_id) is int
    child_closure = await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            org_id=task.org_id,
            thread_id=thread.id,
            turn_id=turn.id,
            skill_run_id=skill_run.id,
            task_id=task.id,
            account_id=thread.account_id,
            project_id=thread.project_id,
            content_item_id=task.content_item_id,
            result_payload={
                "mode": "skill",
                "status": next_status,
                "response": response,
                "task_id": task.id,
                "projections": [
                    {
                        "type": "artifact",
                        "artifact_id": deliverable.id,
                        "artifact_type": output.get("artifact_type"),
                        "skill_run_id": skill_run.id,
                        "account_id": thread.account_id,
                        "report": output.get("report") or {},
                    }
                ],
            },
            skill_output_snapshot=output,
            nested_skill=nested_child,
        ),
        status=next_status,
        message=response,
        error_code=None if approved else "SKILL_APPROVAL_REJECTED",
        commit=not nested_child,
    )
    if nested_child and approved:
        await session.refresh(skill_run)
        await resume_composite_parent(session, child_skill_run=skill_run)
    elif nested_child:
        await session.refresh(skill_run)
        parent_closure = await block_composite_parent_from_child(
            session,
            child_skill_run=skill_run,
            error_code="SKILL_APPROVAL_REJECTED",
        )
        return SkillFinishApprovalResult(
            handled=True,
            publish_intents=(
                parent_closure.publish_intents if parent_closure is not None else ()
            ),
        )
    return SkillFinishApprovalResult(
        handled=True,
        publish_intents=(child_closure.publish_intents if nested_child else ()),
    )
