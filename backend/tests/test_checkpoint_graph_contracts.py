"""Checkpoint graph ownership and contract validation."""

from __future__ import annotations

import pytest

from app.orchestrator.checkpoint_graph_contracts import (
    CheckpointGraphContract,
    CheckpointGraphContractError,
    CheckpointStepSpec,
    get_checkpoint_graph_contract,
    require_checkpoint_graph_contract,
    validate_checkpoint_graph_contract,
)
from app.orchestrator.skills.operation_iteration import OPERATION_ITERATION_SKILL
from app.orchestrator.step_dependencies import OPERATION_LOOP_GRAPH


def test_operation_loop_dependency_facade_is_derived_from_single_registry() -> None:
    contract = require_checkpoint_graph_contract("operation_iteration", 1)

    assert get_checkpoint_graph_contract("operation_iteration", 1) is contract
    assert OPERATION_LOOP_GRAPH.skill_code == contract.skill_code
    assert OPERATION_LOOP_GRAPH.version == contract.graph_version
    assert OPERATION_LOOP_GRAPH.steps == contract.steps
    assert OPERATION_ITERATION_SKILL.checkpoint_graph_key == contract.skill_code
    assert OPERATION_ITERATION_SKILL.checkpoint_graph_version == contract.graph_version


def test_every_operation_step_declares_checkpoint_and_executor_identity() -> None:
    contract = require_checkpoint_graph_contract("operation_iteration", 1)

    assert validate_checkpoint_graph_contract(contract) == tuple(
        step.key for step in contract.steps
    )
    for step in contract.steps:
        assert step.input_schema_version
        assert step.output_schema_version
        assert step.input_projection_key
        assert step.executor_owner in {"native_runtime", "child_skill", "manual"}
        assert step.executor_boundary_key
        if step.reuse_policy == "freshness_bound":
            assert step.freshness_policy_key
        else:
            assert step.freshness_policy_key is None


def test_contract_validation_rejects_missing_checkpoint_identity() -> None:
    invalid_step = CheckpointStepSpec(
        key="unsafe",
        consumes_constraints=frozenset(),
        consumes_outputs=frozenset(),
        produces_outputs=frozenset({"result"}),
        reuse_policy="immutable",
        side_effect_level="none",
        input_schema_version="",
        output_schema_version="output/v1",
        input_projection_key="unsafe/v1",
        freshness_policy_key=None,
        executor_owner="child_skill",
        executor_boundary_key="child_skill:unsafe",
    )
    contract = CheckpointGraphContract(
        skill_code="unsafe_graph",
        skill_version=1,
        graph_version="unsafe/v1",
        steps=(invalid_step,),
    )

    with pytest.raises(CheckpointGraphContractError, match="input_schema_version"):
        validate_checkpoint_graph_contract(contract)


def test_missing_contract_fails_closed() -> None:
    assert get_checkpoint_graph_contract("missing", 1) is None
    with pytest.raises(CheckpointGraphContractError, match="missing:1"):
        require_checkpoint_graph_contract("missing", 1)
