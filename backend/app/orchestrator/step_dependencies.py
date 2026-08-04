"""Pure, fail-closed dependency planning for operation-loop revisions.

This module describes work; it never executes or skips a Skill.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class ConstraintPath(StrEnum):
    GOAL = "goal"
    AUDIENCE = "audience"
    BRAND_CONSTRAINTS = "brand_constraints"
    PRODUCT_FACTS = "product_facts"
    OFFER_TERMS = "offer_terms"
    TOPIC_REQUIREMENTS = "topic_requirements"
    SCRIPT_REQUIREMENTS = "script_requirements"
    VISUAL_REQUIREMENTS = "visual_requirements"
    SCHEDULE_REQUIREMENTS = "schedule_requirements"
    DATA_PERIOD = "data_period"
    SOURCE_ARTIFACTS = "source_artifacts"
    PUBLISH_PARAMETERS = "publish_parameters"


ReusePolicy = Literal["immutable", "freshness_bound", "never"]
SideEffectLevel = Literal[
    "none",
    "read",
    "idempotent_write",
    "non_idempotent_write",
]
InvalidationKind = Literal["direct", "transitive", "fallback"]
InvalidationMode = Literal["partial", "full_recompute"]


@dataclass(frozen=True)
class StepSpec:
    key: str
    consumes_constraints: frozenset[ConstraintPath]
    consumes_outputs: frozenset[str]
    produces_outputs: frozenset[str]
    reuse_policy: ReusePolicy
    side_effect_level: SideEffectLevel


@dataclass(frozen=True)
class OperationLoopGraph:
    skill_code: str
    version: str
    steps: tuple[StepSpec, ...]


@dataclass(frozen=True)
class InvalidationReason:
    step_key: str
    kind: InvalidationKind
    causes: tuple[str, ...]


@dataclass(frozen=True)
class InvalidationPlan:
    skill_code: str
    graph_version: str
    changed_constraints: tuple[str, ...]
    direct_steps: tuple[str, ...]
    transitive_steps: tuple[str, ...]
    affected_steps: tuple[str, ...]
    earliest_affected_step: str | None
    mode: InvalidationMode
    reasons: tuple[InvalidationReason, ...]
    fallback_reason: str | None = None


class DependencyGraphError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


OPERATION_LOOP_SKILL_CODE = "operation_iteration"


def _spec(
    key: str,
    *,
    constraints: tuple[ConstraintPath, ...] = (),
    inputs: tuple[str, ...] = (),
    outputs: tuple[str, ...] = (),
    reuse_policy: ReusePolicy = "immutable",
    side_effect_level: SideEffectLevel = "none",
) -> StepSpec:
    return StepSpec(
        key=key,
        consumes_constraints=frozenset(constraints),
        consumes_outputs=frozenset(inputs),
        produces_outputs=frozenset(outputs),
        reuse_policy=reuse_policy,
        side_effect_level=side_effect_level,
    )


OPERATION_LOOP_GRAPH = OperationLoopGraph(
    skill_code=OPERATION_LOOP_SKILL_CODE,
    version="operation-loop/v1",
    steps=(
        _spec(
            "read_account_data",
            constraints=(ConstraintPath.DATA_PERIOD,),
            outputs=("account_snapshot",),
            reuse_policy="freshness_bound",
            side_effect_level="read",
        ),
        _spec(
            "benchmark_analysis",
            constraints=(ConstraintPath.SOURCE_ARTIFACTS,),
            inputs=("account_snapshot",),
            outputs=("benchmark_findings",),
            reuse_policy="freshness_bound",
            side_effect_level="read",
        ),
        _spec(
            "topic_planning",
            constraints=(
                ConstraintPath.GOAL,
                ConstraintPath.AUDIENCE,
                ConstraintPath.BRAND_CONSTRAINTS,
                ConstraintPath.PRODUCT_FACTS,
                ConstraintPath.TOPIC_REQUIREMENTS,
            ),
            inputs=("account_snapshot", "benchmark_findings"),
            outputs=("topic_plan",),
        ),
        _spec(
            "script_generation",
            constraints=(
                ConstraintPath.BRAND_CONSTRAINTS,
                ConstraintPath.PRODUCT_FACTS,
                ConstraintPath.OFFER_TERMS,
                ConstraintPath.SCRIPT_REQUIREMENTS,
            ),
            inputs=("topic_plan",),
            outputs=("scripts",),
        ),
        _spec(
            "visual_brief_generation",
            constraints=(
                ConstraintPath.BRAND_CONSTRAINTS,
                ConstraintPath.VISUAL_REQUIREMENTS,
            ),
            inputs=("scripts",),
            outputs=("visual_briefs",),
        ),
        _spec(
            "quality_review",
            constraints=(ConstraintPath.BRAND_CONSTRAINTS,),
            inputs=("topic_plan", "scripts", "visual_briefs"),
            outputs=("quality_result",),
            reuse_policy="never",
        ),
        _spec(
            "content_calendar_planning",
            constraints=(ConstraintPath.SCHEDULE_REQUIREMENTS,),
            inputs=("topic_plan", "scripts", "visual_briefs"),
            outputs=("content_calendar",),
        ),
        _spec(
            "publishing_preparation",
            constraints=(
                ConstraintPath.SOURCE_ARTIFACTS,
                ConstraintPath.PUBLISH_PARAMETERS,
            ),
            inputs=("quality_result", "content_calendar"),
            outputs=("publish_package",),
            reuse_policy="never",
            side_effect_level="idempotent_write",
        ),
    ),
)


def validate_dependency_graph(graph: OperationLoopGraph) -> tuple[str, ...]:
    """Validate the graph and return a stable topological order."""

    step_by_key: dict[str, StepSpec] = {}
    producer_by_output: dict[str, str] = {}
    for step in graph.steps:
        if step.key in step_by_key:
            raise DependencyGraphError("duplicate_step", f"Duplicate step: {step.key}")
        step_by_key[step.key] = step
        for output in step.produces_outputs:
            if output in producer_by_output:
                raise DependencyGraphError(
                    "duplicate_producer",
                    f"Output has multiple producers: {output}",
                )
            producer_by_output[output] = step.key

    dependencies: dict[str, set[str]] = {step.key: set() for step in graph.steps}
    for step in graph.steps:
        for output in step.consumes_outputs:
            producer = producer_by_output.get(output)
            if producer is None:
                raise DependencyGraphError(
                    "unknown_output",
                    f"Step {step.key} consumes unknown output: {output}",
                )
            dependencies[step.key].add(producer)

    remaining = {key: set(value) for key, value in dependencies.items()}
    order: list[str] = []
    declaration_order = tuple(step.key for step in graph.steps)
    while remaining:
        ready = tuple(key for key in declaration_order if key in remaining and not remaining[key])
        if not ready:
            raise DependencyGraphError("cycle", "Dependency graph contains a cycle")
        for key in ready:
            order.append(key)
            remaining.pop(key)
        for unresolved in remaining.values():
            unresolved.difference_update(ready)
    return tuple(order)


def affected_steps(
    skill_code: str,
    changed_constraints: set[ConstraintPath | str],
) -> set[str]:
    return set(build_invalidation_plan(skill_code, changed_constraints).affected_steps)


def build_invalidation_plan(
    skill_code: str,
    changed_constraints: set[ConstraintPath | str],
    *,
    dependency_graph: OperationLoopGraph | None = None,
) -> InvalidationPlan:
    graph = dependency_graph or OPERATION_LOOP_GRAPH
    if skill_code != OPERATION_LOOP_SKILL_CODE:
        return _full_recompute_plan(
            skill_code,
            graph,
            changed_constraints,
            fallback_reason="unknown_skill",
        )
    if graph.skill_code != skill_code:
        return _full_recompute_plan(
            skill_code,
            graph,
            changed_constraints,
            fallback_reason="graph_skill_mismatch",
        )
    try:
        normalized = frozenset(ConstraintPath(value) for value in changed_constraints)
    except ValueError:
        return _full_recompute_plan(
            skill_code,
            graph,
            changed_constraints,
            fallback_reason="unknown_constraint",
        )
    try:
        order = validate_dependency_graph(graph)
    except DependencyGraphError as error:
        return _full_recompute_plan(
            skill_code,
            graph,
            changed_constraints,
            fallback_reason=f"invalid_graph:{error.code}",
        )
    step_by_key = {step.key: step for step in graph.steps}
    producer_by_output = {
        output: step.key for step in graph.steps for output in step.produces_outputs
    }

    direct = {
        step.key
        for step in graph.steps
        if step.consumes_constraints.intersection(normalized)
    }
    affected = set(direct)
    for step_key in order:
        step = step_by_key[step_key]
        upstream = {
            producer_by_output[output]
            for output in step.consumes_outputs
            if producer_by_output[output] in affected
        }
        if upstream:
            affected.add(step_key)

    direct_steps = tuple(step_key for step_key in order if step_key in direct)
    transitive_steps = tuple(
        step_key for step_key in order if step_key in affected and step_key not in direct
    )
    ordered_affected = tuple(step_key for step_key in order if step_key in affected)
    reasons: list[InvalidationReason] = []
    for step_key in ordered_affected:
        step = step_by_key[step_key]
        if step_key in direct:
            causes = tuple(
                path.value
                for path in ConstraintPath
                if path in normalized and path in step.consumes_constraints
            )
            reasons.append(InvalidationReason(step_key, "direct", causes))
            continue
        upstream_steps = {
            producer_by_output[output]
            for output in step.consumes_outputs
            if producer_by_output[output] in affected
        }
        causes = tuple(key for key in order if key in upstream_steps)
        reasons.append(InvalidationReason(step_key, "transitive", causes))

    return InvalidationPlan(
        skill_code=skill_code,
        graph_version=graph.version,
        changed_constraints=tuple(
            path.value for path in ConstraintPath if path in normalized
        ),
        direct_steps=direct_steps,
        transitive_steps=transitive_steps,
        affected_steps=ordered_affected,
        earliest_affected_step=ordered_affected[0] if ordered_affected else None,
        mode="partial",
        reasons=tuple(reasons),
    )


def _full_recompute_plan(
    skill_code: str,
    graph: OperationLoopGraph,
    changed_constraints: set[ConstraintPath | str],
    *,
    fallback_reason: str,
) -> InvalidationPlan:
    step_order = tuple(step.key for step in graph.steps)
    constraint_values = tuple(
        sorted(
            value.value if isinstance(value, ConstraintPath) else str(value)
            for value in changed_constraints
        )
    )
    return InvalidationPlan(
        skill_code=skill_code,
        graph_version=graph.version,
        changed_constraints=constraint_values,
        direct_steps=(),
        transitive_steps=(),
        affected_steps=step_order,
        earliest_affected_step=step_order[0] if step_order else None,
        mode="full_recompute",
        reasons=tuple(
            InvalidationReason(step_key, "fallback", (fallback_reason,))
            for step_key in step_order
        ),
        fallback_reason=fallback_reason,
    )


__all__ = [
    "OPERATION_LOOP_GRAPH",
    "OPERATION_LOOP_SKILL_CODE",
    "ConstraintPath",
    "DependencyGraphError",
    "InvalidationPlan",
    "InvalidationReason",
    "OperationLoopGraph",
    "StepSpec",
    "affected_steps",
    "build_invalidation_plan",
    "validate_dependency_graph",
]
