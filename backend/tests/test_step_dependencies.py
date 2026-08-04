"""Pure dependency planning for safe operation-loop recomputation."""

from __future__ import annotations

import pytest

from app.orchestrator.step_dependencies import (
    OPERATION_LOOP_GRAPH,
    OPERATION_LOOP_SKILL_CODE,
    ConstraintPath,
    DependencyGraphError,
    OperationLoopGraph,
    StepSpec,
    affected_steps,
    build_invalidation_plan,
    validate_dependency_graph,
)

EXPECTED_STEP_ORDER = (
    "read_account_data",
    "benchmark_analysis",
    "topic_planning",
    "script_generation",
    "visual_brief_generation",
    "quality_review",
    "content_calendar_planning",
    "publishing_preparation",
)


def _step(
    key: str,
    *,
    consumes: tuple[str, ...] = (),
    produces: tuple[str, ...] = (),
) -> StepSpec:
    return StepSpec(
        key=key,
        consumes_constraints=frozenset(),
        consumes_outputs=frozenset(consumes),
        produces_outputs=frozenset(produces),
        reuse_policy="immutable",
        side_effect_level="none",
    )


def test_operation_loop_contract_is_typed_versioned_and_topological() -> None:
    assert tuple(path.value for path in ConstraintPath) == (
        "goal",
        "audience",
        "brand_constraints",
        "product_facts",
        "offer_terms",
        "topic_requirements",
        "script_requirements",
        "visual_requirements",
        "schedule_requirements",
        "data_period",
        "source_artifacts",
        "publish_parameters",
    )
    assert OPERATION_LOOP_GRAPH.version == "operation-loop/v1"
    assert OPERATION_LOOP_GRAPH.skill_code == OPERATION_LOOP_SKILL_CODE
    assert validate_dependency_graph(OPERATION_LOOP_GRAPH) == EXPECTED_STEP_ORDER
    assert tuple(step.key for step in OPERATION_LOOP_GRAPH.steps) == EXPECTED_STEP_ORDER
    assert all(
        isinstance(path, ConstraintPath)
        for step in OPERATION_LOOP_GRAPH.steps
        for path in step.consumes_constraints
    )


@pytest.mark.parametrize(
    "constraint",
    [ConstraintPath.OFFER_TERMS, ConstraintPath.SCRIPT_REQUIREMENTS],
)
def test_script_or_offer_change_keeps_research_and_invalidates_true_downstream(
    constraint: ConstraintPath,
) -> None:
    plan = build_invalidation_plan(
        OPERATION_LOOP_SKILL_CODE,
        {constraint},
    )

    assert plan.mode == "partial"
    assert plan.direct_steps == ("script_generation",)
    assert plan.transitive_steps == (
        "visual_brief_generation",
        "quality_review",
        "content_calendar_planning",
        "publishing_preparation",
    )
    assert plan.affected_steps == EXPECTED_STEP_ORDER[3:]
    assert plan.earliest_affected_step == "script_generation"
    assert set(EXPECTED_STEP_ORDER[:3]).isdisjoint(
        affected_steps(OPERATION_LOOP_SKILL_CODE, {constraint})
    )


def test_schedule_change_only_invalidates_calendar_and_publish_preparation() -> None:
    plan = build_invalidation_plan(
        OPERATION_LOOP_SKILL_CODE,
        {ConstraintPath.SCHEDULE_REQUIREMENTS},
    )

    assert plan.mode == "partial"
    assert plan.direct_steps == ("content_calendar_planning",)
    assert plan.transitive_steps == ("publishing_preparation",)
    assert plan.affected_steps == (
        "content_calendar_planning",
        "publishing_preparation",
    )
    assert plan.earliest_affected_step == "content_calendar_planning"


def test_data_period_change_invalidates_the_entire_output_chain() -> None:
    plan = build_invalidation_plan(
        OPERATION_LOOP_SKILL_CODE,
        {ConstraintPath.DATA_PERIOD},
    )

    assert plan.mode == "partial"
    assert plan.direct_steps == ("read_account_data",)
    assert plan.transitive_steps == EXPECTED_STEP_ORDER[1:]
    assert plan.affected_steps == EXPECTED_STEP_ORDER
    assert plan.earliest_affected_step == "read_account_data"


@pytest.mark.parametrize(
    ("skill_code", "constraints", "fallback_reason"),
    [
        (OPERATION_LOOP_SKILL_CODE, {"unmodeled_requirement"}, "unknown_constraint"),
        ("unknown_skill", {ConstraintPath.OFFER_TERMS}, "unknown_skill"),
    ],
)
def test_unknown_inputs_fail_closed_to_full_recompute(
    skill_code: str,
    constraints: set[ConstraintPath | str],
    fallback_reason: str,
) -> None:
    plan = build_invalidation_plan(skill_code, constraints)

    assert plan.mode == "full_recompute"
    assert plan.affected_steps == EXPECTED_STEP_ORDER
    assert plan.earliest_affected_step == "read_account_data"
    assert plan.fallback_reason == fallback_reason
    assert affected_steps(skill_code, constraints) == set(EXPECTED_STEP_ORDER)


def test_graph_skill_mismatch_fails_closed_to_full_recompute() -> None:
    mismatched_graph = OperationLoopGraph(
        skill_code="different_operation",
        version=OPERATION_LOOP_GRAPH.version,
        steps=OPERATION_LOOP_GRAPH.steps,
    )

    plan = build_invalidation_plan(
        OPERATION_LOOP_SKILL_CODE,
        {ConstraintPath.OFFER_TERMS},
        dependency_graph=mismatched_graph,
    )

    assert plan.mode == "full_recompute"
    assert plan.affected_steps == EXPECTED_STEP_ORDER
    assert plan.earliest_affected_step == "read_account_data"
    assert plan.fallback_reason == "graph_skill_mismatch"


def test_empty_changed_constraints_is_an_explicit_partial_noop() -> None:
    plan = build_invalidation_plan(OPERATION_LOOP_SKILL_CODE, set())

    assert plan.mode == "partial"
    assert plan.direct_steps == ()
    assert plan.transitive_steps == ()
    assert plan.affected_steps == ()
    assert plan.earliest_affected_step is None
    assert plan.reasons == ()
    assert plan.fallback_reason is None
    assert affected_steps(OPERATION_LOOP_SKILL_CODE, set()) == set()


@pytest.mark.parametrize(
    ("steps", "error_code"),
    [
        (
            (
                _step("first", consumes=("second_output",), produces=("first_output",)),
                _step("second", consumes=("first_output",), produces=("second_output",)),
            ),
            "cycle",
        ),
        (
            (
                _step("first", produces=("shared",)),
                _step("second", produces=("shared",)),
            ),
            "duplicate_producer",
        ),
        ((_step("first", consumes=("missing",)),), "unknown_output"),
    ],
)
def test_invalid_graphs_fail_closed_without_partial_plan(
    steps: tuple[StepSpec, ...],
    error_code: str,
) -> None:
    graph = OperationLoopGraph(
        skill_code=OPERATION_LOOP_SKILL_CODE,
        version="invalid/v1",
        steps=steps,
    )

    with pytest.raises(DependencyGraphError) as error:
        validate_dependency_graph(graph)
    assert error.value.code == error_code

    plan = build_invalidation_plan(
        OPERATION_LOOP_SKILL_CODE,
        {ConstraintPath.GOAL},
        dependency_graph=graph,
    )
    assert plan.mode == "full_recompute"
    assert plan.earliest_affected_step == "first"
    assert plan.affected_steps == tuple(step.key for step in steps)
    assert plan.fallback_reason == f"invalid_graph:{error_code}"


def test_direct_and_transitive_reasons_are_stable_and_auditable() -> None:
    plan = build_invalidation_plan(
        OPERATION_LOOP_SKILL_CODE,
        {ConstraintPath.OFFER_TERMS, ConstraintPath.SCRIPT_REQUIREMENTS},
    )

    assert tuple(
        (reason.step_key, reason.kind, reason.causes) for reason in plan.reasons
    ) == (
        (
            "script_generation",
            "direct",
            ("offer_terms", "script_requirements"),
        ),
        ("visual_brief_generation", "transitive", ("script_generation",)),
        (
            "quality_review",
            "transitive",
            ("script_generation", "visual_brief_generation"),
        ),
        (
            "content_calendar_planning",
            "transitive",
            ("script_generation", "visual_brief_generation"),
        ),
        (
            "publishing_preparation",
            "transitive",
            ("quality_review", "content_calendar_planning"),
        ),
    )
