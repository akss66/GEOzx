"""Private operation lineage must never weaken public artifact approval rules."""

from dataclasses import replace

import pytest

from app.models import BrainTask, ContentItem, Deliverable, SkillRun
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
