"""Server-only provenance for pending artifacts inside one operation root."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BrainTask, ContentItem, Deliverable, RunRevision, SkillRun
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


@dataclass(frozen=True)
class OperationRevisionBridge:
    """Server-proven reuse proof for one downstream-only operation revision."""

    revision_id: int
    source_run_id: int
    source_turn_id: int
    source_parent_skill_run_id: int
    topic_ref: OperationLineageRef
    previous_graph: list[dict[str, object]]
    source_server_context: dict[str, object]
    reused_steps: tuple[str, ...]


async def resolve_operation_revision_bridge(
    session: AsyncSession,
    *,
    scope: RuntimeScope,
    current_parent_skill_run_id: int,
) -> OperationRevisionBridge | None:
    """Resolve the sole allowed cross-run bridge for script-forward revisions."""

    revision = await session.scalar(
        select(RunRevision).where(
            RunRevision.revision_run_id == scope.run_id,
            RunRevision.revision_skill_run_id == current_parent_skill_run_id,
        )
    )
    if revision is None:
        return None
    affected = tuple(str(item) for item in revision.affected_steps)
    if (
        revision.mode != "partial"
        or revision.org_id != scope.org_id
        or revision.account_id != scope.account_id
        or revision.thread_id != scope.thread_id
        or revision.task_id != scope.task_id
        or revision.revision_turn_id != scope.turn_id
        or revision.changed_constraints != {"offer_terms": {"operation": "changed"}}
        or tuple(revision.direct_affected_steps) != ("script_generation",)
        or "script_generation" not in affected
        or any(
            step in affected
            for step in ("read_account_data", "benchmark_analysis", "topic_planning")
        )
    ):
        raise PermissionError("OPERATION_REVISION_BRIDGE_SCOPE_MISMATCH")
    source_parent = await session.get(SkillRun, revision.source_skill_run_id)
    current_parent = await session.get(SkillRun, current_parent_skill_run_id)
    task = await session.get(BrainTask, scope.task_id)
    if (
        source_parent is None
        or current_parent is None
        or task is None
        or task.content_item_id is None
        or source_parent.skill_code != "operation_iteration"
        or current_parent.skill_code != "operation_iteration"
        or source_parent.org_id != scope.org_id
        or current_parent.org_id != scope.org_id
        or source_parent.thread_id != scope.thread_id
        or current_parent.thread_id != scope.thread_id
        or source_parent.turn_id != revision.source_turn_id
        or current_parent.turn_id != scope.turn_id
        or source_parent.run_id != revision.source_run_id
        or current_parent.run_id != scope.run_id
        or source_parent.task_id != scope.task_id
        or current_parent.task_id != scope.task_id
    ):
        raise PermissionError("OPERATION_REVISION_BRIDGE_PARENT_MISMATCH")
    source_output = dict(source_parent.output_snapshot or {})
    report = source_output.get("report")
    graph = report.get("child_skill_graph") if isinstance(report, dict) else None
    server_context = source_output.get("_server_context")
    if not isinstance(graph, list) or not isinstance(server_context, dict):
        raise PermissionError("OPERATION_REVISION_BRIDGE_SOURCE_MISSING")
    by_code = {
        str(node.get("skill_code")): node
        for node in graph
        if isinstance(node, dict) and isinstance(node.get("skill_code"), str)
    }
    required_codes = {
        "topic_planning",
        "script_generation",
        "visual_brief_generation",
        "content_calendar_planning",
        "publishing_preparation",
    }
    topic_node = by_code.get("topic_planning")
    topic_artifact_id = topic_node.get("artifact_id") if topic_node else None
    if (
        set(by_code) != required_codes
        or topic_node is None
        or topic_node.get("status") != "completed"
        or type(topic_artifact_id) is not int
    ):
        raise PermissionError("OPERATION_REVISION_BRIDGE_GRAPH_INVALID")
    topic_artifact = await session.get(Deliverable, topic_artifact_id)
    topic_child = (
        await session.get(SkillRun, topic_artifact.skill_run_id)
        if topic_artifact is not None and topic_artifact.skill_run_id is not None
        else None
    )
    content = (
        await session.get(ContentItem, topic_artifact.content_item_id)
        if topic_artifact is not None
        else None
    )
    if (
        topic_artifact is None
        or topic_child is None
        or content is None
        or topic_artifact.type.value != "topic_plan"
        or topic_artifact.content_item_id != task.content_item_id
        or topic_artifact.thread_id != scope.thread_id
        or topic_artifact.turn_id != revision.source_turn_id
        or topic_artifact.run_id != revision.source_run_id
        or content.account_id != scope.account_id
        or topic_child.run_id != revision.source_run_id
        or topic_child.turn_id != revision.source_turn_id
        or topic_child.task_id != scope.task_id
        or topic_child.status != "completed"
        or dict(topic_child.output_snapshot or {}).get("composite_parent_skill_run_id")
        != source_parent.id
        or topic_artifact.status
        not in {DeliverableStatus.PENDING_REVIEW, DeliverableStatus.APPROVED}
    ):
        raise PermissionError("OPERATION_REVISION_BRIDGE_TOPIC_INVALID")
    previous_graph = deepcopy(graph)
    affected_child_codes = set(affected) & required_codes
    for node in previous_graph:
        if node.get("skill_code") in affected_child_codes:
            node["status"] = "pending"
            node["artifact_id"] = None
            node["error_code"] = None
            node["terminal_reason"] = None
    return OperationRevisionBridge(
        revision_id=revision.id,
        source_run_id=revision.source_run_id,
        source_turn_id=revision.source_turn_id,
        source_parent_skill_run_id=source_parent.id,
        topic_ref=OperationLineageRef(
            artifact_id=topic_artifact.id,
            version=topic_artifact.version,
            source_skill_run_id=topic_child.id,
            parent_skill_run_id=source_parent.id,
        ),
        previous_graph=previous_graph,
        source_server_context=deepcopy(server_context),
        reused_steps=("read_account_data", "benchmark_analysis", "topic_planning"),
    )


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
    cross_parent_refs = [
        ref for ref in refs if ref.parent_skill_run_id != expected_parent_skill_run_id
    ]
    revision_bridge = None
    if cross_parent_refs:
        revision_bridge = await resolve_operation_revision_bridge(
            session,
            scope=scope,
            current_parent_skill_run_id=expected_parent_skill_run_id,
        )
        if (
            revision_bridge is None
            or len(cross_parent_refs) != 1
            or cross_parent_refs[0] != revision_bridge.topic_ref
        ):
            raise PermissionError("OPERATION_LINEAGE_CROSS_RUN_MISMATCH")
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
        cross_run = ref.parent_skill_run_id != expected_parent_skill_run_id
        expected_run_id = (
            revision_bridge.source_run_id
            if cross_run and revision_bridge is not None
            else scope.run_id
        )
        expected_turn_id = (
            revision_bridge.source_turn_id
            if cross_run and revision_bridge is not None
            else scope.turn_id
        )
        if (
            deliverable.version != ref.version
            or deliverable.skill_run_id != ref.source_skill_run_id
            or deliverable.content_item_id != task.content_item_id
            or deliverable.thread_id != scope.thread_id
            or deliverable.turn_id != expected_turn_id
            or deliverable.run_id != expected_run_id
            or content.account_id != scope.account_id
            or child.id != ref.source_skill_run_id
            or child.org_id != scope.org_id
            or child.thread_id != scope.thread_id
            or child.turn_id != expected_turn_id
            or child.run_id != expected_run_id
            or child.task_id != scope.task_id
            or child.status != "completed"
            or dict(child.output_snapshot or {}).get("composite_parent_skill_run_id")
            != ref.parent_skill_run_id
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


__all__ = [
    "OperationLineageRef",
    "OperationRevisionBridge",
    "resolve_internal_lineage_artifacts",
    "resolve_operation_revision_bridge",
]
