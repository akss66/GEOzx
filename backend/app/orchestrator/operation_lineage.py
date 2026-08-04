"""Server-only provenance for pending artifacts inside one operation root."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BrainTask, ContentItem, Deliverable, SkillRun
from app.models.enums import DeliverableStatus
from app.orchestrator.runtime_scope import RuntimeScope, RuntimeScopeConflict


@dataclass(frozen=True)
class OperationLineageRef:
    artifact_id: int
    version: int
    source_skill_run_id: int
    parent_skill_run_id: int

    def __post_init__(self) -> None:
        values = (
            self.artifact_id,
            self.version,
            self.source_skill_run_id,
            self.parent_skill_run_id,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("operation lineage values must be positive integers")


async def resolve_internal_lineage_artifacts(
    session: AsyncSession,
    *,
    refs: list[OperationLineageRef],
    expected_parent_skill_run_id: int,
    expected_source_artifact_ids: list[int],
    scope: RuntimeScope,
) -> list[dict[str, object]]:
    """Resolve exact same-root artifacts without making them publicly approved."""

    try:
        await scope.validate(session)
    except RuntimeScopeConflict as exc:
        raise PermissionError("OPERATION_LINEAGE_RUNTIME_SCOPE_MISMATCH") from exc
    artifact_ids = [ref.artifact_id for ref in refs]
    expected_ids = list(dict.fromkeys(expected_source_artifact_ids))
    if (
        not refs
        or artifact_ids != expected_ids
        or len(set(artifact_ids)) != len(artifact_ids)
        or any(ref.parent_skill_run_id != expected_parent_skill_run_id for ref in refs)
    ):
        raise PermissionError("OPERATION_LINEAGE_COVERAGE_MISMATCH")

    task = await session.get(BrainTask, scope.task_id)
    if task is None or task.content_item_id is None:
        raise PermissionError("OPERATION_LINEAGE_TASK_MISMATCH")
    parent = await session.get(SkillRun, expected_parent_skill_run_id)
    if (
        parent is None
        or parent.org_id != scope.org_id
        or parent.thread_id != scope.thread_id
        or parent.turn_id != scope.turn_id
        or parent.run_id != scope.run_id
        or parent.task_id != scope.task_id
        or parent.skill_code != "operation_iteration"
    ):
        raise PermissionError("OPERATION_LINEAGE_PARENT_MISMATCH")
    rows = list(
        await session.execute(
            select(Deliverable, ContentItem, SkillRun)
            .join(ContentItem, ContentItem.id == Deliverable.content_item_id)
            .join(SkillRun, SkillRun.id == Deliverable.skill_run_id)
            .where(Deliverable.id.in_(artifact_ids))
        )
    )
    by_id = {deliverable.id: (deliverable, content, child) for deliverable, content, child in rows}
    if set(by_id) != set(artifact_ids):
        raise PermissionError("OPERATION_LINEAGE_ARTIFACT_NOT_FOUND")

    resolved: list[dict[str, object]] = []
    refs_by_id = {ref.artifact_id: ref for ref in refs}
    for artifact_id in artifact_ids:
        deliverable, content, child = by_id[artifact_id]
        ref = refs_by_id[artifact_id]
        if (
            deliverable.version != ref.version
            or deliverable.skill_run_id != ref.source_skill_run_id
            or deliverable.content_item_id != task.content_item_id
            or deliverable.thread_id != scope.thread_id
            or deliverable.turn_id != scope.turn_id
            or deliverable.run_id != scope.run_id
            or content.account_id != scope.account_id
            or child.id != ref.source_skill_run_id
            or child.org_id != scope.org_id
            or child.thread_id != scope.thread_id
            or child.turn_id != scope.turn_id
            or child.run_id != scope.run_id
            or child.task_id != scope.task_id
            or child.status != "completed"
            or dict(child.output_snapshot or {}).get("composite_parent_skill_run_id")
            != expected_parent_skill_run_id
            or deliverable.status
            not in {DeliverableStatus.PENDING_REVIEW, DeliverableStatus.APPROVED}
        ):
            raise PermissionError("OPERATION_LINEAGE_SCOPE_MISMATCH")
        resolved.append(
            {
                "artifact_id": deliverable.id,
                "artifact_type": deliverable.type.value,
                "version": deliverable.version,
                "payload": dict(deliverable.payload or {}),
            }
        )
    return resolved


__all__ = ["OperationLineageRef", "resolve_internal_lineage_artifacts"]
