"""Private operation lineage must never weaken public artifact approval rules."""

from dataclasses import replace

import pytest

from app.models import (
    AgentRun,
    BrainTask,
    ContentItem,
    ConversationTurn,
    Deliverable,
    RunRevision,
    SkillRun,
)
from app.models.enums import (
    AgentCode,
    BrainTaskStatus,
    BrainTaskType,
    DeliverableStatus,
    DeliverableType,
)
from app.orchestrator.operation_lineage import (
    OperationLineageRef,
    resolve_internal_lineage_artifacts,
    resolve_operation_revision_bridge,
)
from app.orchestrator.runtime_scope import RuntimeScope
from app.orchestrator.skill_runtime import _confirmed_source_artifacts
from tests.test_operating_skills import _scope


async def _lineage_scope(session, admin):
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="operation-lineage",
        message="生成下周内容",
    )
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title="下周内容",
    )
    session.add(content)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title="下周内容",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
    )
    session.add(task)
    await session.flush()
    run.task_id = task.id
    parent = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key="lineage-parent",
        skill_code="operation_iteration",
        skill_version=1,
        status="running",
        input_snapshot={"account_id": account.id},
        output_snapshot={},
    )
    session.add(parent)
    await session.flush()
    child = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key="lineage-child",
        skill_code="script_generation",
        skill_version=1,
        status="completed",
        input_snapshot={"account_id": account.id},
        output_snapshot={"composite_parent_skill_run_id": parent.id},
    )
    session.add(child)
    await session.flush()
    artifact = Deliverable(
        content_item_id=content.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        skill_run_id=child.id,
        agent_code=AgentCode.CONTENT_DIRECTOR.value,
        type=DeliverableType.VIDEO_SCRIPT,
        version=1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"scripts": [{"script_id": "script-01"}]},
    )
    session.add(artifact)
    await session.commit()
    scope = RuntimeScope(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        skill_run_id=child.id,
    )
    ref = OperationLineageRef(
        artifact_id=artifact.id,
        version=artifact.version,
        source_skill_run_id=child.id,
        parent_skill_run_id=parent.id,
    )
    return account, parent, child, artifact, scope, ref


async def _bridge_scope(session, admin):
    account, thread, source_turn, source_run = await _scope(
        session,
        admin,
        key="operation-bridge",
        message="生成下周内容",
    )
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title="operation-bridge",
    )
    session.add(content)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title="operation-bridge",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
    )
    session.add(task)
    await session.flush()
    source_run.task_id = task.id
    source_parent = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=source_turn.id,
        run_id=source_run.id,
        task_id=task.id,
        idempotency_key="bridge-source-parent",
        skill_code="operation_iteration",
        skill_version=1,
        status="completed",
        input_snapshot={"account_id": account.id},
        output_snapshot={},
    )
    session.add(source_parent)
    await session.flush()
    topic_child = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=source_turn.id,
        run_id=source_run.id,
        task_id=task.id,
        idempotency_key="bridge-topic-child",
        skill_code="topic_planning",
        skill_version=1,
        status="completed",
        input_snapshot={"account_id": account.id},
        output_snapshot={"composite_parent_skill_run_id": source_parent.id},
    )
    session.add(topic_child)
    await session.flush()
    topic_artifact = Deliverable(
        content_item_id=content.id,
        thread_id=thread.id,
        turn_id=source_turn.id,
        run_id=source_run.id,
        skill_run_id=topic_child.id,
        agent_code=AgentCode.CONTENT_DIRECTOR.value,
        type=DeliverableType.TOPIC_PLAN,
        version=1,
        status=DeliverableStatus.APPROVED,
        payload={"topics": [{"topic_id": "topic-01"}]},
    )
    session.add(topic_artifact)
    await session.flush()
    source_parent.output_snapshot = {
        "report": {
            "child_skill_graph": [
                {
                    "skill_code": "topic_planning",
                    "status": "completed",
                    "artifact_id": topic_artifact.id,
                },
                {
                    "skill_code": "script_generation",
                    "status": "completed",
                    "artifact_id": None,
                },
                {
                    "skill_code": "visual_brief_generation",
                    "status": "completed",
                    "artifact_id": None,
                },
                {
                    "skill_code": "content_calendar_planning",
                    "status": "completed",
                    "artifact_id": None,
                },
                {
                    "skill_code": "publishing_preparation",
                    "status": "completed",
                    "artifact_id": None,
                },
            ]
        },
        "_server_context": {
            "preloaded_tool_results": {},
            "tool_audit_refs": {},
            "lineage_refs": {},
        },
    }
    revision_turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="bridge-revision-turn",
        user_input="修改第一条脚本",
        status="running",
        target_turn_id=source_turn.id,
    )
    session.add(revision_turn)
    await session.flush()
    revision_run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=revision_turn.id,
        client_message_id="bridge-revision-run",
        status="queued",
        phase="queued",
        request_payload={"operation": "execute_revision", "task_id": task.id},
        task_id=task.id,
    )
    session.add(revision_run)
    await session.flush()
    revision_parent = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=revision_turn.id,
        run_id=revision_run.id,
        task_id=task.id,
        idempotency_key="bridge-revision-parent",
        skill_code="operation_iteration",
        skill_version=1,
        status="running",
        input_snapshot={"account_id": account.id},
        output_snapshot={},
    )
    session.add(revision_parent)
    await session.flush()
    revision = RunRevision(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        task_id=task.id,
        source_turn_id=source_turn.id,
        source_run_id=source_run.id,
        source_skill_run_id=source_parent.id,
        revision_turn_id=revision_turn.id,
        revision_run_id=revision_run.id,
        revision_skill_run_id=revision_parent.id,
        mode="partial",
        status="planned",
        dependency_graph_version="operation_iteration:v1",
        earliest_affected_step="script_generation",
        changed_constraints={"offer_terms": {"operation": "changed"}},
        direct_affected_steps=["script_generation"],
        affected_steps=[
            "script_generation",
            "visual_brief_generation",
            "content_calendar_planning",
            "publishing_preparation",
        ],
        reused_steps=[],
        plan_hash="b" * 64,
    )
    session.add(revision)
    await session.commit()
    scope = RuntimeScope(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=revision_turn.id,
        run_id=revision_run.id,
        task_id=task.id,
        skill_run_id=revision_parent.id,
    )
    return scope, revision, source_parent, topic_artifact, revision_parent


@pytest.mark.asyncio
async def test_internal_lineage_resolves_same_parent_pending_artifact(session, admin):
    account, parent, _child, artifact, scope, ref = await _lineage_scope(session, admin)

    resolved = await resolve_internal_lineage_artifacts(
        session,
        refs=[ref],
        expected_parent_skill_run_id=parent.id,
        expected_source_artifact_ids=[artifact.id],
        scope=scope,
    )

    assert resolved == [
        {
            "artifact_id": artifact.id,
            "artifact_type": "video_script",
            "version": 1,
            "payload": {"scripts": [{"script_id": "script-01"}]},
        }
    ]
    with pytest.raises(PermissionError, match="SOURCE_ARTIFACT_NOT_APPROVED"):
        await _confirmed_source_artifacts(
            session,
            account_id=account.id,
            artifact_ids=[artifact.id],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", 2),
        ("source_skill_run_id", 999_999),
        ("parent_skill_run_id", 999_999),
    ],
)
async def test_internal_lineage_rejects_stale_or_unrelated_refs(
    session,
    admin,
    field,
    value,
):
    _account, parent, _child, artifact, scope, ref = await _lineage_scope(session, admin)

    with pytest.raises(PermissionError):
        await resolve_internal_lineage_artifacts(
            session,
            refs=[replace(ref, **{field: value})],
            expected_parent_skill_run_id=parent.id,
            expected_source_artifact_ids=[artifact.id],
            scope=scope,
        )


@pytest.mark.asyncio
async def test_internal_lineage_rejects_cross_runtime_scope(session, admin):
    _account, parent, _child, artifact, scope, ref = await _lineage_scope(session, admin)

    with pytest.raises(PermissionError):
        await resolve_internal_lineage_artifacts(
            session,
            refs=[ref],
            expected_parent_skill_run_id=parent.id,
            expected_source_artifact_ids=[artifact.id],
            scope=replace(scope, run_id=scope.run_id + 1),
        )


@pytest.mark.asyncio
async def test_operation_revision_bridge_returns_none_for_noneligible_revision(
    session, admin
):
    scope, revision, _source_parent, _topic_artifact, revision_parent = await _bridge_scope(
        session, admin
    )
    revision.mode = "full_recompute"
    revision.changed_constraints = {"unknown_constraint": {"operation": "changed"}}
    revision.direct_affected_steps = []
    revision.affected_steps = [
        "read_account_data",
        "benchmark_analysis",
        "topic_planning",
        "script_generation",
    ]
    await session.commit()

    resolved = await resolve_operation_revision_bridge(
        session,
        scope=scope,
        current_parent_skill_run_id=revision_parent.id,
    )

    assert resolved is None


@pytest.mark.asyncio
async def test_operation_revision_bridge_rejects_eligible_lineage_mismatch(
    session, admin
):
    scope, _revision, source_parent, _topic_artifact, revision_parent = await _bridge_scope(
        session, admin
    )
    source_parent.output_snapshot = {
        **dict(source_parent.output_snapshot or {}),
        "report": {
            **dict(dict(source_parent.output_snapshot or {}).get("report") or {}),
            "child_skill_graph": [
                node
                for node in dict(source_parent.output_snapshot or {})["report"][
                    "child_skill_graph"
                ]
                if node["skill_code"] != "publishing_preparation"
            ],
        },
    }
    await session.commit()

    with pytest.raises(PermissionError, match="OPERATION_REVISION_BRIDGE_GRAPH_INVALID"):
        await resolve_operation_revision_bridge(
            session,
            scope=scope,
            current_parent_skill_run_id=revision_parent.id,
        )
