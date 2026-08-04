"""Exact-lineage, atomic final checkpoint service tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

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
    StageDataEnvelope,
)
from app.services import skill_stage_checkpoints as checkpoint_service
from app.services.checkpoint_freshness import FreshnessVerdict
from app.services.checkpoint_hashing import (
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
        data_watermark_hash="e" * 64 if step.reuse_policy == "freshness_bound" else None,
        freshness_expires_at=(
            datetime.now(UTC) + timedelta(hours=1)
            if step.reuse_policy == "freshness_bound"
            else None
        ),
        side_effect_level=step.side_effect_level,
        manual_reconciliation_required=False,
        finalized_at=datetime.now(UTC),
    )


async def _revision(session, scopes):
    return await create_revision_record(
        session,
        source_scope=scopes.source,
        revision_scope=scopes.revision,
        invalidation=build_invalidation_plan("operation_iteration", {ConstraintPath.OFFER_TERMS}),
    )


def _allow_freshness(monkeypatch) -> None:
    async def _always_reusable(*_args, **_kwargs):
        return FreshnessVerdict(
            kind="reusable",
            reason=None,
            validated_at=(
                datetime.now(UTC)
                if _kwargs["step"].reuse_policy == "freshness_bound"
                else None
            ),
        )

    monkeypatch.setattr(
        checkpoint_service, "assess_checkpoint_freshness", _always_reusable
    )


def _canonical_reuse_fixtures(scopes):
    step_keys = ("read_account_data", "benchmark_analysis", "topic_planning")
    checkpoints = tuple(_source_checkpoint(scopes, step_key) for step_key in step_keys)
    inputs = {step_key: _envelopes(step_key)[1] for step_key in step_keys}
    return checkpoints, inputs


async def test_prepare_reuses_only_exact_completed_source_lineage(
    session, admin, monkeypatch
) -> None:
    scopes = await _lineage(session, admin, suffix="exact-source")
    _allow_freshness(monkeypatch)
    sources, expected_inputs = _canonical_reuse_fixtures(scopes)
    session.add_all(sources)
    await session.flush()
    revision = await _revision(session, scopes)
    _, _, output = _envelopes("topic_planning")

    result = await prepare_revision_execution(
        session,
        revision_scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_inputs=ExpectedStageInputs(values=expected_inputs),
    )

    assert isinstance(result, PartialExecution)
    topic_binding = next(
        binding for binding in result.reused if binding.step_key == "topic_planning"
    )
    assert topic_binding.source_checkpoint_id == sources[-1].id
    assert result.hydrated_outputs["topic_planning"] == output
    reused = await session.get(SkillStageCheckpoint, topic_binding.checkpoint_id)
    assert reused.status == "reused"
    assert reused.source_stage_status == "completed"
    assert reused.output_snapshot is None
    resolved = await load_latest_stage_output(
        session, scope=scopes.revision, step_key="topic_planning"
    )
    assert resolved.checkpoint_id == reused.id
    assert resolved.source_checkpoint_id == sources[-1].id


async def test_wrong_run_candidate_is_not_selected_by_latest_lookup(
    session, admin, monkeypatch
) -> None:
    scopes = await _lineage(session, admin, suffix="wrong-run")
    _allow_freshness(monkeypatch)
    sources, expected_inputs = _canonical_reuse_fixtures(scopes)
    read_source, benchmark_source, _topic_source = sources
    wrong = _source_checkpoint(scopes, "topic_planning")
    wrong.run_id = scopes.revision.run_id
    wrong.turn_id = scopes.revision.turn_id
    wrong.skill_run_id = scopes.revision.skill_run_id
    session.add_all([read_source, benchmark_source, wrong])
    await session.flush()
    revision = await _revision(session, scopes)

    result = await prepare_revision_execution(
        session,
        revision_scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_inputs=ExpectedStageInputs(values=expected_inputs),
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
    session, admin, monkeypatch
) -> None:
    scopes = await _lineage(session, admin, suffix="atomic")
    _allow_freshness(monkeypatch)
    sources, expected_inputs = _canonical_reuse_fixtures(scopes)
    first, second, third = sources
    third.output_hash = "f" * 64
    session.add_all([first, second, third])
    await session.flush()
    revision = await _revision(session, scopes)

    result = await prepare_revision_execution(
        session,
        revision_scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_inputs=ExpectedStageInputs(values=expected_inputs),
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


async def test_source_input_snapshot_is_revalidated_before_reuse(
    session, admin, monkeypatch
) -> None:
    scopes = await _lineage(session, admin, suffix="unsafe-input")
    _allow_freshness(monkeypatch)
    sources, expected_inputs = _canonical_reuse_fixtures(scopes)
    source = sources[-1]
    source.input_snapshot = {
        "schema_version": "topic_planning-input/v1",
        "data": {"secret": "must-not-cross-boundary"},
    }
    session.add_all(sources)
    await session.flush()
    revision = await _revision(session, scopes)

    result = await prepare_revision_execution(
        session,
        revision_scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_inputs=ExpectedStageInputs(values=expected_inputs),
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
    revision = await _revision(session, scopes)
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


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("success", "non_idempotent_effect_completed"),
        ("ambiguous", "external_write_ambiguous"),
    ],
)
async def test_manual_write_ledger_precedes_dependency_full_recompute(
    session, admin, status, reason
) -> None:
    scopes = await _lineage(session, admin, suffix=f"full-ledger-{status}")
    tool_call = AgentToolCall(
        org_id=scopes.source.org_id,
        task_id=scopes.source.task_id,
        skill_run_id=scopes.source.skill_run_id,
        thread_id=scopes.source.thread_id,
        turn_id=scopes.source.turn_id,
        module="skill",
        tool_code="publish_content",
        tool_name="Publish content",
        idempotency_key=f"full-ledger-{status}",
        side_effect_level="non_idempotent_write",
        status=status,
        permission_mode="auto",
        requires_human_confirmation=False,
        input_summary="",
        output_summary="",
        meta={},
    )
    session.add(tool_call)
    await session.flush()
    revision = await create_revision_record(
        session,
        source_scope=scopes.source,
        revision_scope=scopes.revision,
        invalidation=build_invalidation_plan(
            "operation_iteration", {"unknown_constraint"}
        ),
    )

    result = await prepare_revision_execution(
        session,
        revision_scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_inputs=ExpectedStageInputs(values={}),
    )

    assert result.kind == "manual_reconciliation"
    assert result.reason == reason
    assert result.blocking_receipt_ids == (tool_call.id,)
    assert revision.mode == "manual_reconciliation"
    assert revision.reused_steps == []


async def test_source_manual_checkpoint_precedes_dependency_full_recompute(
    session, admin
) -> None:
    scopes = await _lineage(session, admin, suffix="full-source-manual")
    source = _source_checkpoint(scopes, "quality_review")
    source.manual_reconciliation_required = True
    source.reuse_policy = "never"
    session.add(source)
    await session.flush()
    revision = await create_revision_record(
        session,
        source_scope=scopes.source,
        revision_scope=scopes.revision,
        invalidation=build_invalidation_plan(
            "operation_iteration", {"unknown_constraint"}
        ),
    )

    result = await prepare_revision_execution(
        session,
        revision_scope=scopes.revision,
        revision_id=revision.id,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_inputs=ExpectedStageInputs(values={}),
    )

    assert result.kind == "manual_reconciliation"
    assert result.reason == "source_checkpoint_manual"
    assert revision.mode == "manual_reconciliation"
    assert revision.reused_steps == []


async def test_checkpoint_services_reject_non_registry_contract_instances(
    session, admin
) -> None:
    scopes = await _lineage(session, admin, suffix="forged-contract")
    revision = await _revision(session, scopes)
    canonical = require_checkpoint_graph_contract("operation_iteration", 1)
    forged_step = replace(
        next(step for step in canonical.steps if step.key == "script_generation"),
        executor_boundary_key="child_skill:forged",
    )
    forged = replace(
        canonical,
        steps=tuple(
            forged_step if step.key == forged_step.key else step for step in canonical.steps
        ),
    )
    step, input_value, output_value = _envelopes("script_generation")

    with pytest.raises(CheckpointServiceConflict) as prepare_error:
        await prepare_revision_execution(
            session,
            revision_scope=scopes.revision,
            revision_id=revision.id,
            contract=forged,
            expected_inputs=ExpectedStageInputs(values={}),
        )
    assert prepare_error.value.code == "CHECKPOINT_GRAPH_CONTRACT_MISSING"

    with pytest.raises(CheckpointServiceConflict) as write_error:
        await record_completed_stage(
            session,
            scope=scopes.revision,
            revision_id=revision.id,
            contract=forged,
            draft=CompletedStageDraft(
                step_key=step.key,
                input=input_value,
                output=output_value,
            ),
        )
    assert write_error.value.code == "CHECKPOINT_GRAPH_CONTRACT_MISSING"


async def test_completed_stage_write_is_strict_transaction_neutral_and_resolvable(
    session, admin, monkeypatch
) -> None:
    scopes = await _lineage(session, admin, suffix="completed")
    revision = await _revision(session, scopes)
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


async def test_langgraph_checkpoint_id_is_idempotent_across_stage_revisions(
    session, admin
) -> None:
    scopes = await _lineage(session, admin, suffix="semantic-replay")
    step, input_value, first_output = _envelopes("script_generation")
    second_output = StageDataEnvelope(
        schema_version=step.output_schema_version,
        data={"scripts": {"safe": True, "revision": 2}},
    )
    first_draft = CompletedStageDraft(
        step_key=step.key,
        input=input_value,
        output=first_output,
        langgraph_checkpoint_id="semantic-1",
    )
    first = await record_completed_stage(
        session,
        scope=scopes.revision,
        revision_id=None,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        draft=first_draft,
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
            langgraph_checkpoint_id="semantic-2",
        ),
    )
    replay = await record_completed_stage(
        session,
        scope=scopes.revision,
        revision_id=None,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        draft=first_draft,
    )

    assert first.checkpoint_id != second.checkpoint_id
    assert replay.created is False
    assert replay.checkpoint_id == first.checkpoint_id
    assert (
        await session.scalar(
            select(func.count(SkillStageCheckpoint.id)).where(
                SkillStageCheckpoint.skill_run_id == scopes.revision.skill_run_id,
                SkillStageCheckpoint.step_key == step.key,
            )
        )
        == 2
    )


async def test_langgraph_checkpoint_id_replay_with_changed_fact_conflicts(
    session, admin
) -> None:
    scopes = await _lineage(session, admin, suffix="semantic-conflict")
    step, input_value, output_value = _envelopes("script_generation")
    await record_completed_stage(
        session,
        scope=scopes.revision,
        revision_id=None,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        draft=CompletedStageDraft(
            step_key=step.key,
            input=input_value,
            output=output_value,
            langgraph_checkpoint_id="semantic-conflict",
        ),
    )
    changed = StageDataEnvelope(
        schema_version=step.output_schema_version,
        data={"scripts": {"safe": False}},
    )

    with pytest.raises(CheckpointServiceConflict) as error:
        await record_completed_stage(
            session,
            scope=scopes.revision,
            revision_id=None,
            contract=require_checkpoint_graph_contract("operation_iteration", 1),
            draft=CompletedStageDraft(
                step_key=step.key,
                input=input_value,
                output=changed,
                langgraph_checkpoint_id="semantic-conflict",
            ),
        )
    assert error.value.code == "CHECKPOINT_IMMUTABILITY_CONFLICT"
