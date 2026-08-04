"""Deterministic revision resolution policy tests."""

from __future__ import annotations

from app.orchestrator.checkpoint_graph_contracts import require_checkpoint_graph_contract
from app.orchestrator.step_dependencies import ConstraintPath, build_invalidation_plan
from app.schemas.run_revision import NoRevisionRequired
from app.services.run_revisions import CheckpointCandidateVerdict, resolve_revision_policy


def _candidate(
    step_key: str,
    *,
    outcome: str = "reusable",
    reason: str | None = None,
    source_run_id: int = 10,
    source_turn_id: int = 20,
    source_skill_run_id: int = 30,
) -> CheckpointCandidateVerdict:
    return CheckpointCandidateVerdict(
        step_key=step_key,
        checkpoint_id=100 + len(step_key),
        source_run_id=source_run_id,
        source_turn_id=source_turn_id,
        source_skill_run_id=source_skill_run_id,
        outcome=outcome,
        reason=reason,
    )


def _candidates(*, outcome_by_step: dict[str, tuple[str, str | None]] | None = None):
    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    overrides = outcome_by_step or {}
    return tuple(
        _candidate(
            step.key,
            outcome=overrides.get(step.key, ("reusable", None))[0],
            reason=overrides.get(step.key, ("reusable", None))[1],
        )
        for step in contract.steps
        if step.reuse_policy != "never"
    )


def test_empty_diff_returns_no_revision_required() -> None:
    resolution = resolve_revision_policy(
        invalidation=build_invalidation_plan("operation_iteration", set()),
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_source_run_id=10,
        expected_source_turn_id=20,
        expected_source_skill_run_id=30,
        candidates=(),
    )

    assert isinstance(resolution, NoRevisionRequired)


def test_manual_precedes_full_and_full_precedes_partial() -> None:
    invalidation = build_invalidation_plan(
        "operation_iteration", {ConstraintPath.SCHEDULE_REQUIREMENTS}
    )
    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    candidates = _candidates(
        outcome_by_step={
            "read_account_data": ("full_recompute", "freshness_expired"),
            "benchmark_analysis": ("manual_reconciliation", "external_write_ambiguous"),
        }
    )

    resolution = resolve_revision_policy(
        invalidation=invalidation,
        contract=contract,
        expected_source_run_id=10,
        expected_source_turn_id=20,
        expected_source_skill_run_id=30,
        candidates=candidates,
    )

    assert resolution.mode == "manual_reconciliation"
    assert resolution.reason == "external_write_ambiguous"
    assert resolution.execute_steps == ()
    assert resolution.reused_steps == ()


def test_manual_candidate_precedes_dependency_full_recompute() -> None:
    resolution = resolve_revision_policy(
        invalidation=build_invalidation_plan("operation_iteration", {"unknown_constraint"}),
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_source_run_id=10,
        expected_source_turn_id=20,
        expected_source_skill_run_id=30,
        candidates=_candidates(
            outcome_by_step={
                "benchmark_analysis": (
                    "manual_reconciliation",
                    "external_write_ambiguous",
                )
            }
        ),
    )

    assert resolution.mode == "manual_reconciliation"
    assert resolution.reason == "external_write_ambiguous"
    assert resolution.execute_steps == ()
    assert resolution.reused_steps == ()


def test_manual_candidate_precedes_ambiguous_candidate_full_fallback() -> None:
    candidates = list(
        _candidates(
            outcome_by_step={
                "benchmark_analysis": (
                    "manual_reconciliation",
                    "external_write_ambiguous",
                )
            }
        )
    )
    first = candidates[0]
    candidates.append(
        _candidate(first.step_key, source_run_id=999)
    )

    resolution = resolve_revision_policy(
        invalidation=build_invalidation_plan(
            "operation_iteration", {ConstraintPath.SCHEDULE_REQUIREMENTS}
        ),
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_source_run_id=10,
        expected_source_turn_id=20,
        expected_source_skill_run_id=30,
        candidates=candidates,
    )

    assert resolution.mode == "manual_reconciliation"
    assert resolution.reason == "external_write_ambiguous"
    assert resolution.execute_steps == ()
    assert resolution.reused_steps == ()


def test_never_policy_executes_and_never_creates_reuse_binding() -> None:
    invalidation = build_invalidation_plan(
        "operation_iteration", {ConstraintPath.SCHEDULE_REQUIREMENTS}
    )
    resolution = resolve_revision_policy(
        invalidation=invalidation,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_source_run_id=10,
        expected_source_turn_id=20,
        expected_source_skill_run_id=30,
        candidates=_candidates(),
    )

    assert resolution.mode == "partial"
    assert "quality_review" in resolution.execute_steps
    assert "publishing_preparation" in resolution.execute_steps
    assert "quality_review" not in resolution.reused_steps
    assert "publishing_preparation" not in resolution.reused_steps
    assert len(resolution.source_checkpoint_ids) == len(resolution.reused_steps)
    candidates = {candidate.step_key: candidate for candidate in _candidates()}
    assert resolution.source_checkpoint_ids == tuple(
        candidates[step_key].checkpoint_id for step_key in resolution.reused_steps
    )


def test_candidate_from_wrong_source_lineage_fails_closed() -> None:
    invalidation = build_invalidation_plan(
        "operation_iteration", {ConstraintPath.SCHEDULE_REQUIREMENTS}
    )
    candidates = list(_candidates())
    candidates[0] = _candidate(candidates[0].step_key, source_run_id=999)

    resolution = resolve_revision_policy(
        invalidation=invalidation,
        contract=require_checkpoint_graph_contract("operation_iteration", 1),
        expected_source_run_id=10,
        expected_source_turn_id=20,
        expected_source_skill_run_id=30,
        candidates=candidates,
    )

    assert resolution.mode == "full_recompute"
    assert resolution.reason == "checkpoint_source_lineage_mismatch"
    assert resolution.reused_steps == ()


def test_plan_hash_is_deterministic_when_candidate_order_changes() -> None:
    invalidation = build_invalidation_plan(
        "operation_iteration", {ConstraintPath.SCHEDULE_REQUIREMENTS}
    )
    arguments = {
        "invalidation": invalidation,
        "contract": require_checkpoint_graph_contract("operation_iteration", 1),
        "expected_source_run_id": 10,
        "expected_source_turn_id": 20,
        "expected_source_skill_run_id": 30,
    }
    candidates = _candidates()

    forward = resolve_revision_policy(**arguments, candidates=candidates)
    reversed_order = resolve_revision_policy(**arguments, candidates=tuple(reversed(candidates)))

    assert forward.plan_hash == reversed_order.plan_hash
    assert forward == reversed_order
