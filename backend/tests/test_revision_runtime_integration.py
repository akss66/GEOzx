"""Runtime integration contracts for revision execution."""

from app.orchestrator.checkpoint_graph_contracts import (
    require_checkpoint_graph_contract,
)
from app.orchestrator.skill_runtime import resolve_revision_executor_boundaries


def test_operation_iteration_maps_only_real_native_or_child_boundaries() -> None:
    contract = require_checkpoint_graph_contract("operation_iteration", 1)

    mapping = resolve_revision_executor_boundaries(contract)

    assert mapping.native_boundaries == frozenset({"prepare_deliverable"})
    assert mapping.logical_boundaries == {
        "read_account_data": "child_skill:account_inspection",
        "benchmark_analysis": "child_skill:performance_review",
        "topic_planning": "child_skill:topic_planning",
        "script_generation": "child_skill:script_generation",
        "visual_brief_generation": "child_skill:visual_brief_generation",
        "quality_review": "native_runtime:prepare_deliverable",
        "content_calendar_planning": "child_skill:content_calendar_planning",
        "publishing_preparation": "child_skill:publishing_preparation",
    }
    assert mapping.requires_full_recompute is False


def test_logical_graph_cannot_claim_a_fabricated_native_stage() -> None:
    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    native_claim = contract.steps[0].__class__(
        **{
            **contract.steps[0].__dict__,
            "executor_owner": "native_runtime",
            "executor_boundary_key": "native_runtime:read_account_data",
        }
    )
    forged = contract.__class__(
        skill_code=contract.skill_code,
        skill_version=contract.skill_version,
        graph_version=contract.graph_version,
        steps=(native_claim, *contract.steps[1:]),
    )

    mapping = resolve_revision_executor_boundaries(forged)

    assert mapping.logical_boundaries["read_account_data"] is None
    assert mapping.requires_full_recompute is True
