"""Exact-lineage, atomic final checkpoint service tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models import AgentToolCall, SkillStageCheckpoint
from app.orchestrator.checkpoint_graph_contracts import require_checkpoint_graph_contract
from app.orchestrator.step_dependencies import ConstraintPath, build_invalidation_plan
from app.schemas.run_revision import (
    CompletedStageDraft,
    ExpectedStageInputs,
    FullRecompute,
    PartialExecution,
    RevisionResolution,
    StageDataEnvelope,
)
from app.services.checkpoint_hashing import (
    revision_plan_hash,
    stage_contract_hash,
    stage_input_hash,
    stage_output_hash,
)
from app.services.run_revisions import create_revision_record
from app.services.skill_stage_checkpoints import (
    CheckpointServiceConflict,
    load_latest_stage_output,
    prepare_revision_execution,
    record_completed_stage,
)
from tests.test_run_revisions_service import _lineage


def _envelopes(step_key: str):
    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    step = next(item for item in contract.steps if item.key == step_key)
    input_value = StageDataEnvelope(
        schema_version=step.input_schema_version,
        data={"revision_constraints": {"goal": "growth"}},
    )
    output_value = StageDataEnvelope(
        schema_version=step.output_schema_version,
        data={key: {"safe": True} for key in step.produces_outputs},
    )
    return step, input_value, output_value


def _source_checkpoint(scopes, step_key: str, *, output_hash_override: str | None = None):
    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    step, input_value, output_value = _envelopes(step_key)
    return SkillStageCheckpoint(
        org_id=scopes.source.org_id,
        account_id=scopes.source.account_id,
        thread_id=scopes.source.thread_id,
        turn_id=scopes.source.turn_id,
        task_id=scopes.source.task_id,
        run_id=scopes.source.run_id,
        skill_run_id=scopes.source.skill_run_id,
        run_revision_id=None,
        step_key=step.key,
        stage_revision=1,
        status="completed",
        skill_code=contract.skill_code,
        skill_version=contract.skill_version,
        dependency_graph_version=contract.graph_version,
        stage_contract_hash=stage_contract_hash(contract=contract, step=step),
        input_snapshot=input_value.model_dump(mode="json"),
        input_hash=stage_input_hash(input_value),
        output_snapshot=output_value.model_dump(mode="json"),
        output_hash=output_hash_override or stage_output_hash(output_value),
        source_artifact_refs=[],
        evidence_refs=[],
        reuse_policy=step.reuse_policy,
        side_effect_level=step.side_effect_level,
        manual_reconciliation_required=False,
        finalized_at=datetime.now(UTC),
    )


def _resolution(*, reused_steps: tuple[str, ...], execute_steps: tuple[str, ...]):
    payload = {
        "mode": "partial",
        "execute_steps": list(execute_steps),
        "reused_steps": list(reused_steps),
    }
    return RevisionResolution(
        mode="partial",
        reason=None,
        execute_steps=execute_steps,
        reused_steps=reused_steps,
        source_checkpoint_ids=tuple(range(100, 100 + len(reused_steps))),
        blocking_receipt_ids=(),
        plan_hash=revision_plan_hash(payload),
    )


async def _revision(session, scopes, reused_steps: tuple[str, ...]):
    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    execute = tuple(step.key for step in contract.steps if step.key not in reused_steps)
    return await create_revision_record(
        session,
        source_scope=scopes.source,
        revision_scope=scopes.revision,
        invalidation=build_invalidation_plan("operation_iteration", {ConstraintPath.OFFER_TERMS}),
        resolution=_resolution(reused_steps=reused_steps, execute_steps=execute),
    )


async def test_prepare_reuses_only_exact_completed_source_lineage(session, admin) -> None:
    scopes = await _lineage(session, admin, suffix="exact-source")
    source = _source_checkpoint(scopes, "topic_planning")
    session.add(source)
    await session.flush()
    revision = await _revision(session, scopes, ("topic_planning",))
    _, expected, output = _envelopes("topic_planning")

    result = await prepare_revision_execution(
        session,
        revision_scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_inputs=ExpectedStageInputs(values={"topic_planning": expected}),
    )

    assert isinstance(result, PartialExecution)
    assert tuple(binding.source_checkpoint_id for binding in result.reused) == (source.id,)
    assert result.hydrated_outputs == {"topic_planning": output}
    reused = await session.get(SkillStageCheckpoint, result.reused[0].checkpoint_id)
    assert reused.status == "reused"
    assert reused.source_stage_status == "completed"
    assert reused.output_snapshot is None
    resolved = await load_latest_stage_output(
        session, scope=scopes.revision, step_key="topic_planning"
    )
    assert resolved.checkpoint_id == reused.id
    assert resolved.source_checkpoint_id == source.id


async def test_wrong_run_candidate_is_not_selected_by_latest_lookup(session, admin) -> None:
    scopes = await _lineage(session, admin, suffix="wrong-run")
    wrong = _source_checkpoint(scopes, "topic_planning")
    wrong.run_id = scopes.revision.run_id
    wrong.turn_id = scopes.revision.turn_id
    wrong.skill_run_id = scopes.revision.skill_run_id
    session.add(wrong)
    await session.flush()
    revision = await _revision(session, scopes, ("topic_planning",))
    _, expected, _ = _envelopes("topic_planning")

    result = await prepare_revision_execution(
        session,
        revision_scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_inputs=ExpectedStageInputs(values={"topic_planning": expected}),
    )

    assert isinstance(result, FullRecompute)
    assert result.reason == "checkpoint_missing"
    assert (
        await session.scalar(
            select(func.count(SkillStageCheckpoint.id)).where(
                SkillStageCheckpoint.status == "reused"
            )
        )
        == 0
    )


async def test_reuse_barrier_is_all_or_nothing_when_late_candidate_is_corrupt(
    session, admin
) -> None:
    scopes = await _lineage(session, admin, suffix="atomic")
    first = _source_checkpoint(scopes, "topic_planning")
    second = _source_checkpoint(scopes, "content_calendar_planning", output_hash_override="f" * 64)
    session.add_all([first, second])
    await session.flush()
    revision = await _revision(session, scopes, ("topic_planning", "content_calendar_planning"))
    _, first_input, _ = _envelopes("topic_planning")
    _, second_input, _ = _envelopes("content_calendar_planning")

    result = await prepare_revision_execution(
        session,
        revision_scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_inputs=ExpectedStageInputs(
            values={
                "topic_planning": first_input,
                "content_calendar_planning": second_input,
            }
        ),
    )

    assert isinstance(result, FullRecompute)
    assert result.reason == "checkpoint_output_corrupt"
    assert (
        await session.scalar(
            select(func.count(SkillStageCheckpoint.id)).where(
                SkillStageCheckpoint.status == "reused"
            )
        )
        == 0
    )


async def test_source_input_snapshot_is_revalidated_before_reuse(session, admin) -> None:
    scopes = await _lineage(session, admin, suffix="unsafe-input")
    source = _source_checkpoint(scopes, "topic_planning")
    source.input_snapshot = {
        "schema_version": "topic_planning-input/v1",
        "data": {"secret": "must-not-cross-boundary"},
    }
    session.add(source)
    await session.flush()
    revision = await _revision(session, scopes, ("topic_planning",))
    _, expected, _ = _envelopes("topic_planning")

    result = await prepare_revision_execution(
        session,
        revision_scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_inputs=ExpectedStageInputs(values={"topic_planning": expected}),
    )

    assert isinstance(result, FullRecompute)
    assert result.reason == "checkpoint_input_mismatch"
    assert (
        await session.scalar(
            select(func.count(SkillStageCheckpoint.id)).where(
                SkillStageCheckpoint.status == "reused"
            )
        )
        == 0
    )


async def test_manual_side_effect_verdict_precedes_reuse_and_writes_zero_rows(
    session, admin
) -> None:
    scopes = await _lineage(session, admin, suffix="manual-wins")
    source = _source_checkpoint(scopes, "topic_planning")
    tool_call = AgentToolCall(
        org_id=scopes.source.org_id,
        task_id=scopes.source.task_id,
        skill_run_id=scopes.source.skill_run_id,
        thread_id=scopes.source.thread_id,
        turn_id=scopes.source.turn_id,
        module="skill",
        tool_code="publish_content",
        tool_name="Publish content",
        idempotency_key="manual-wins",
        side_effect_level="non_idempotent_write",
        status="success",
        permission_mode="auto",
        requires_human_confirmation=False,
        input_summary="",
        output_summary="",
        meta={},
    )
    session.add_all([source, tool_call])
    await session.flush()
    revision = await _revision(session, scopes, ("topic_planning",))
    _, expected, _ = _envelopes("topic_planning")

    result = await prepare_revision_execution(
        session,
        revision_scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_inputs=ExpectedStageInputs(values={"topic_planning": expected}),
    )

    assert result.kind == "manual_reconciliation"
    assert result.reason == "non_idempotent_effect_completed"
    assert result.blocking_receipt_ids == (tool_call.id,)
    assert (
        await session.scalar(
            select(func.count(SkillStageCheckpoint.id)).where(
                SkillStageCheckpoint.status == "reused"
            )
        )
        == 0
    )


async def test_completed_stage_write_is_strict_transaction_neutral_and_resolvable(
    session, admin, monkeypatch
) -> None:
    scopes = await _lineage(session, admin, suffix="completed")
    revision = await _revision(session, scopes, ())
    step, input_value, output_value = _envelopes("script_generation")

    async def _forbid_commit():
        raise AssertionError("checkpoint service must not commit")

    monkeypatch.setattr(session, "commit", _forbid_commit)
    result = await record_completed_stage(
        session,
        scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        draft=CompletedStageDraft(
            step_key=step.key,
            input=input_value,
            output=output_value,
        ),
    )
    replay = await record_completed_stage(
        session,
        scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        draft=CompletedStageDraft(
            step_key=step.key,
            input=input_value,
            output=output_value,
        ),
    )
    resolved = await load_latest_stage_output(session, scope=scopes.revision, step_key=step.key)

    assert result.created is True
    assert replay.created is False
    assert result.checkpoint_id == replay.checkpoint_id == resolved.checkpoint_id
    assert resolved.output == output_value


async def test_completed_stage_rejects_unregistered_step(session, admin) -> None:
    scopes = await _lineage(session, admin, suffix="unknown-step")
    with pytest.raises(CheckpointServiceConflict) as error:
        await record_completed_stage(
            session,
            scope=scopes.revision,
            revision_id=None,
            contract=require_checkpoint_graph_contract("operation_iteration", 1),
            draft=CompletedStageDraft(
                step_key="unregistered",
                input=StageDataEnvelope(schema_version="input/v1", data={}),
                output=StageDataEnvelope(schema_version="output/v1", data={}),
            ),
        )
    assert error.value.code == "CHECKPOINT_GRAPH_CONTRACT_MISSING"


async def test_completed_stage_derives_manual_flag_from_durable_write_ledger(
    session, admin
) -> None:
    scopes = await _lineage(session, admin, suffix="completed-manual")
    step, input_value, output_value = _envelopes("script_generation")
    tool_call = AgentToolCall(
        org_id=scopes.revision.org_id,
        task_id=scopes.revision.task_id,
        skill_run_id=scopes.revision.skill_run_id,
        thread_id=scopes.revision.thread_id,
        turn_id=scopes.revision.turn_id,
        module="skill",
        tool_code="external_write",
        tool_name="External write",
        idempotency_key="completed-manual",
        side_effect_level="non_idempotent_write",
        status="success",
        permission_mode="auto",
        requires_human_confirmation=False,
        input_summary="",
        output_summary="",
        meta={},
    )
    session.add(tool_call)
    await session.flush()

    result = await record_completed_stage(
        session,
        scope=scopes.revision,
        revision_id=None,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        draft=CompletedStageDraft(
            step_key=step.key,
            input=input_value,
            output=output_value,
        ),
    )

    checkpoint = await session.get(SkillStageCheckpoint, result.checkpoint_id)
    assert checkpoint.manual_reconciliation_required is True
    assert checkpoint.reuse_policy == "never"
    assert checkpoint.side_effect_level == "non_idempotent_write"


async def test_new_completed_fact_gets_next_revision_and_latest_output_wins(
    session, admin
) -> None:
    scopes = await _lineage(session, admin, suffix="next-stage-revision")
    step, input_value, first_output = _envelopes("script_generation")
    second_output = StageDataEnvelope(
        schema_version=step.output_schema_version,
        data={"scripts": {"safe": True, "revision": 2}},
    )
    first = await record_completed_stage(
        session,
        scope=scopes.revision,
        revision_id=None,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        draft=CompletedStageDraft(
            step_key=step.key,
            input=input_value,
            output=first_output,
            langgraph_checkpoint_id="stage-call-1",
        ),
    )
    second = await record_completed_stage(
        session,
        scope=scopes.revision,
        revision_id=None,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        draft=CompletedStageDraft(
            step_key=step.key,
            input=input_value,
            output=second_output,
            langgraph_checkpoint_id="stage-call-2",
        ),
    )

    first_row = await session.get(SkillStageCheckpoint, first.checkpoint_id)
    second_row = await session.get(SkillStageCheckpoint, second.checkpoint_id)
    latest = await load_latest_stage_output(
        session, scope=scopes.revision, step_key=step.key
    )
    assert (first_row.stage_revision, second_row.stage_revision) == (1, 2)
    assert latest.checkpoint_id == second.checkpoint_id
    assert latest.output == second_output
