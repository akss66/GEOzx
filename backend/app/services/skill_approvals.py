"""Approval convergence for V3 Skills that pause before completion."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentScheduleEntry,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    Event,
    RunRevision,
    SkillRun,
)
from app.models.enums import DeliverableStatus, DeliverableType
from app.orchestrator.skills.operating_tasks import WeeklyOperationPackage
from app.services.composite_skill_runs import (
    block_composite_parent_from_child,
    resume_composite_parent,
)
from app.services.runtime_locking import (
    RuntimeRootLock,
    require_runtime_root_lock,
)
from app.services.runtime_state import (
    RuntimePublishIntent,
    RuntimeStateScope,
    close_runtime_state,
)


class SkillApprovalConflict(RuntimeError):
    """Persisted Skill approval provenance cannot be safely reconciled."""


def _schedule_signature(
    *,
    scheduled_at: datetime,
    timezone: str,
    content_item_id: int,
    created_by_id: int,
) -> tuple[str, str, int, int]:
    try:
        zone = ZoneInfo(timezone)
    except (KeyError, ValueError) as exc:
        raise SkillApprovalConflict("SKILL_APPROVAL_SCHEDULE_CONFLICT") from exc
    localized = (
        scheduled_at.replace(tzinfo=zone)
        if scheduled_at.tzinfo is None or scheduled_at.utcoffset() is None
        else scheduled_at
    )
    return (
        localized.astimezone(UTC).isoformat(timespec="microseconds"),
        timezone,
        content_item_id,
        created_by_id,
    )


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
    prelocked: RuntimeRootLock | None,
) -> SkillFinishApprovalResult:
    """Close a typed `before_finish` Skill without owning an outer commit."""

    if tool_call.skill_run_id is None:
        return SkillFinishApprovalResult(handled=False)
    meta = dict(tool_call.meta or {})
    if meta.get("approval_stage") != "before_finish":
        return SkillFinishApprovalResult(handled=False)
    if prelocked is None:
        raise SkillApprovalConflict("SKILL_APPROVAL_RUNTIME_NOT_PRELOCKED")

    skill_run = await session.get(SkillRun, tool_call.skill_run_id)
    if skill_run is None:
        raise SkillApprovalConflict("SKILL_APPROVAL_RUN_MISSING")
    require_runtime_root_lock(
        session,
        prelocked,
        run_id=skill_run.run_id,
        turn_id=skill_run.turn_id,
        task_id=task.id,
        content_item_id=task.content_item_id,
        skill_run_id=skill_run.id,
        deliverable_id=(meta["artifact_id"] if type(meta.get("artifact_id")) is int else None),
        tool_call_id=tool_call.id,
    )
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
    schedule_entry_ids: tuple[int, ...] = ()
    schedule_publish_intents: tuple[RuntimePublishIntent, ...] = ()
    if approved and isinstance(dict(deliverable.payload or {}).get("package"), dict):
        schedule_entry_ids, schedule_intent = await _create_manual_schedule_entries_for_package(
            session,
            tool_call=tool_call,
            task=task,
            thread=thread,
            deliverable=deliverable,
        )
        schedule_publish_intents = (schedule_intent,)
    next_status = "completed" if approved else "blocked"
    response = (
        "已创建 5 条手动发布任务；请按安排完成拍摄和人工发布，并在发布后记录结果。"
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
                "schedule_entry_ids": list(schedule_entry_ids),
            },
        }
    )
    deliverable.status = DeliverableStatus.APPROVED if approved else DeliverableStatus.REJECTED
    nested_parent_id = dict(skill_run.output_snapshot or {}).get("composite_parent_skill_run_id")
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
        commit=False,
        prelocked=prelocked,
    )
    if nested_child and approved:
        await session.refresh(skill_run)
        await resume_composite_parent(session, child_skill_run=skill_run, prelocked=prelocked)
    elif nested_child:
        await session.refresh(skill_run)
        parent_closure = await block_composite_parent_from_child(
            session,
            child_skill_run=skill_run,
            error_code="SKILL_APPROVAL_REJECTED",
            prelocked=prelocked,
        )
        return SkillFinishApprovalResult(
            handled=True,
            publish_intents=(parent_closure.publish_intents if parent_closure is not None else ())
            + schedule_publish_intents,
        )
    return SkillFinishApprovalResult(
        handled=True,
        publish_intents=child_closure.publish_intents + schedule_publish_intents,
    )


async def _create_manual_schedule_entries_for_package(
    session: AsyncSession,
    *,
    tool_call: AgentToolCall,
    task: BrainTask,
    thread: ConversationThread,
    deliverable: Deliverable,
) -> tuple[tuple[int, ...], RuntimePublishIntent]:
    """Create the five manual-publish rows inside the locked approval transaction."""

    try:
        package = WeeklyOperationPackage.model_validate(
            dict(deliverable.payload or {}).get("package")
        )
    except ValueError as exc:
        raise SkillApprovalConflict("SKILL_APPROVAL_PACKAGE_INVALID") from exc
    publish_slots = [item for item in package.calendar_slots if item.slot_type == "publish"]
    buffer_slots = [item for item in package.calendar_slots if item.slot_type == "review_buffer"]
    script_ids = [item.script_id for item in publish_slots]
    if (
        len(publish_slots) != 5
        or len(buffer_slots) != 2
        or any(item.scheduled_at is None for item in publish_slots)
        or any(item.script_id is not None for item in buffer_slots)
        or any(item.scheduled_at is not None for item in buffer_slots)
        or len(script_ids) != len(set(script_ids))
        or set(script_ids) != {item.script_id for item in package.scripts}
        or task.content_item_id != deliverable.content_item_id
        or task.created_by_id is None
    ):
        raise SkillApprovalConflict("SKILL_APPROVAL_PACKAGE_SLOTS_INVALID")

    existing = list(
        await session.scalars(
            select(ContentScheduleEntry)
            .where(
                ContentScheduleEntry.org_id == task.org_id,
                ContentScheduleEntry.account_id == thread.account_id,
                ContentScheduleEntry.source_artifact_id == deliverable.id,
                ContentScheduleEntry.source_artifact_version == deliverable.version,
            )
            .order_by(ContentScheduleEntry.id)
        )
    )
    expected_signatures = sorted(
        _schedule_signature(
            scheduled_at=slot.scheduled_at,
            timezone=slot.timezone,
            content_item_id=deliverable.content_item_id,
            created_by_id=task.created_by_id,
        )
        for slot in publish_slots
        if slot.scheduled_at is not None
    )

    revision_source = await _lock_revision_source_schedule_entries(
        session,
        task=task,
        thread=thread,
        deliverable=deliverable,
    )

    def require_exact_schedule(rows: list[ContentScheduleEntry]) -> None:
        actual_signatures = sorted(
            _schedule_signature(
                scheduled_at=item.scheduled_at,
                timezone=item.timezone,
                content_item_id=item.content_item_id,
                created_by_id=item.created_by_id,
            )
            for item in rows
        )
        if len(rows) != 5 or actual_signatures != expected_signatures:
            raise SkillApprovalConflict("SKILL_APPROVAL_SCHEDULE_CONFLICT")

    if existing:
        require_exact_schedule(existing)
        rows = existing

    else:
        rows = [
            ContentScheduleEntry(
                org_id=task.org_id,
                account_id=thread.account_id,
                content_item_id=deliverable.content_item_id,
                source_artifact_id=deliverable.id,
                source_artifact_version=deliverable.version,
                created_by_id=task.created_by_id,
                scheduled_at=slot.scheduled_at,
                timezone=slot.timezone,
                status="planned",
            )
            for slot in publish_slots
        ]
        try:
            async with session.begin_nested():
                session.add_all(rows)
                await session.flush()
        except IntegrityError as exc:
            concurrent = list(
                await session.scalars(
                    select(ContentScheduleEntry)
                    .where(
                        ContentScheduleEntry.org_id == task.org_id,
                        ContentScheduleEntry.account_id == thread.account_id,
                        ContentScheduleEntry.source_artifact_id == deliverable.id,
                        ContentScheduleEntry.source_artifact_version == deliverable.version,
                    )
                    .order_by(ContentScheduleEntry.id)
                )
            )
            if len(concurrent) != 5:
                raise SkillApprovalConflict("SKILL_APPROVAL_SCHEDULE_CONFLICT") from exc
            require_exact_schedule(concurrent)
            rows = concurrent

    if revision_source is not None:
        source_rows, source_artifact = revision_source
        for row in source_rows:
            row.status = "superseded"
        source_artifact.status = DeliverableStatus.SUPERSEDED
        await session.flush()

    raw_key = (
        f"operation-schedule-v1:{task.org_id}:{deliverable.id}:{deliverable.version}:{tool_call.id}"
    )
    event_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    event = await session.scalar(select(Event).where(Event.idempotency_key == event_key))
    if event is None:
        event = Event(
            type="pending_work.updated",
            org_id=task.org_id,
            account_id=thread.account_id,
            content_item_id=deliverable.content_item_id,
            project_id=thread.project_id,
            thread_id=thread.id,
            turn_id=deliverable.turn_id,
            run_id=deliverable.run_id,
            skill_run_id=deliverable.skill_run_id,
            payload={"account_id": thread.account_id},
            idempotency_key=event_key,
        )
        session.add(event)
        await session.flush()
    return (
        tuple(item.id for item in rows),
        RuntimePublishIntent(
            event_id=event.id,
            event_type=event.type,
            turn_id=None,
        ),
    )


async def _lock_revision_source_schedule_entries(
    session: AsyncSession,
    *,
    task: BrainTask,
    thread: ConversationThread,
    deliverable: Deliverable,
) -> tuple[list[ContentScheduleEntry], Deliverable] | None:
    """Lock and validate the source package schedules for one exact revision."""

    if deliverable.run_id is None or deliverable.skill_run_id is None:
        return None
    revision = await session.scalar(
        select(RunRevision).where(RunRevision.revision_run_id == deliverable.run_id)
    )
    if revision is None:
        return None
    current_child = await session.get(SkillRun, deliverable.skill_run_id)
    current_parent_id = (
        dict(current_child.output_snapshot or {}).get("composite_parent_skill_run_id")
        if current_child is not None
        else None
    )
    if (
        revision.mode not in {"partial", "full_recompute"}
        or revision.org_id != task.org_id
        or revision.account_id != thread.account_id
        or revision.thread_id != thread.id
        or revision.task_id != task.id
        or revision.revision_turn_id != deliverable.turn_id
        or revision.revision_skill_run_id != current_parent_id
        or revision.source_skill_run_id is None
        or current_child is None
        or current_child.skill_code != "publishing_preparation"
        or current_child.run_id != deliverable.run_id
        or current_child.turn_id != deliverable.turn_id
        or current_child.task_id != task.id
        or deliverable.type != DeliverableType.PUBLISH_PACKAGE
    ):
        raise SkillApprovalConflict("SKILL_APPROVAL_REVISION_SCOPE_CONFLICT")

    source_parent = await session.get(SkillRun, revision.source_skill_run_id)
    source_report = (
        dict(source_parent.output_snapshot or {}).get("report")
        if source_parent is not None
        else None
    )
    source_graph = (
        source_report.get("child_skill_graph") if isinstance(source_report, dict) else None
    )
    publishing_nodes = [
        node
        for node in source_graph or []
        if isinstance(node, dict) and node.get("skill_code") == "publishing_preparation"
    ]
    source_artifact_id = (
        publishing_nodes[0].get("artifact_id") if len(publishing_nodes) == 1 else None
    )
    source_artifact = (
        await session.get(Deliverable, source_artifact_id)
        if type(source_artifact_id) is int
        else None
    )
    source_child = (
        await session.get(SkillRun, source_artifact.skill_run_id)
        if source_artifact is not None and source_artifact.skill_run_id is not None
        else None
    )
    if (
        source_parent is None
        or source_parent.id != revision.source_skill_run_id
        or source_parent.skill_code != "operation_iteration"
        or source_parent.run_id != revision.source_run_id
        or source_parent.turn_id != revision.source_turn_id
        or source_parent.thread_id != thread.id
        or source_parent.task_id != task.id
        or source_artifact is None
        or source_artifact.type != DeliverableType.PUBLISH_PACKAGE
        or source_artifact.status
        not in {DeliverableStatus.APPROVED, DeliverableStatus.SUPERSEDED}
        or source_artifact.content_item_id != deliverable.content_item_id
        or source_artifact.thread_id != thread.id
        or source_artifact.turn_id != revision.source_turn_id
        or source_artifact.run_id != revision.source_run_id
        or source_child is None
        or source_child.skill_code != "publishing_preparation"
        or source_child.run_id != revision.source_run_id
        or source_child.turn_id != revision.source_turn_id
        or source_child.task_id != task.id
        or dict(source_child.output_snapshot or {}).get("composite_parent_skill_run_id")
        != source_parent.id
    ):
        raise SkillApprovalConflict("SKILL_APPROVAL_REVISION_SOURCE_CONFLICT")

    source_state = source_artifact.status
    try:
        source_package = WeeklyOperationPackage.model_validate(
            dict(source_artifact.payload or {}).get("package")
        )
    except ValueError as exc:
        raise SkillApprovalConflict("SKILL_APPROVAL_REVISION_SOURCE_CONFLICT") from exc
    source_slots = [item for item in source_package.calendar_slots if item.slot_type == "publish"]
    expected = sorted(
        _schedule_signature(
            scheduled_at=slot.scheduled_at,
            timezone=slot.timezone,
            content_item_id=source_artifact.content_item_id,
            created_by_id=task.created_by_id,
        )
        for slot in source_slots
        if slot.scheduled_at is not None and task.created_by_id is not None
    )
    source_rows = list(
        await session.scalars(
            select(ContentScheduleEntry)
            .where(
                ContentScheduleEntry.org_id == task.org_id,
                ContentScheduleEntry.account_id == thread.account_id,
                ContentScheduleEntry.source_artifact_id == source_artifact.id,
                ContentScheduleEntry.source_artifact_version == source_artifact.version,
            )
            .order_by(ContentScheduleEntry.id)
            .with_for_update()
        )
    )
    actual = sorted(
        _schedule_signature(
            scheduled_at=row.scheduled_at,
            timezone=row.timezone,
            content_item_id=row.content_item_id,
            created_by_id=row.created_by_id,
        )
        for row in source_rows
    )
    if source_state == DeliverableStatus.SUPERSEDED:
        if (
            source_parent.status != "stopped"
            or source_parent.error_code != "SUPERSEDED_BY_REVISION"
            or source_child.status != "stopped"
            or source_child.error_code != "SUPERSEDED_BY_REVISION"
            or source_rows
        ):
            raise SkillApprovalConflict("SKILL_APPROVAL_REVISION_SOURCE_CONFLICT")
        return [], source_artifact
    if len(source_slots) != 5 or len(source_rows) != 5 or actual != expected:
        raise SkillApprovalConflict("SKILL_APPROVAL_REVISION_SCHEDULE_CONFLICT")
    statuses = {row.status for row in source_rows}
    if statuses == {"superseded"} and all(row.published_at is None for row in source_rows):
        return source_rows, source_artifact
    if statuses != {"planned"} or any(row.published_at is not None for row in source_rows):
        raise SkillApprovalConflict("SKILL_APPROVAL_REVISION_SCHEDULE_PUBLISHED")
    return source_rows, source_artifact
