"""Pure, fail-closed dependency planning for operation-loop revisions.

This module describes work; it never executes or skips a Skill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.orchestrator.checkpoint_graph_contracts import (
    CheckpointStepSpec,
    ConstraintPath,
    require_checkpoint_graph_contract,
)

InvalidationKind = Literal["direct", "transitive", "fallback"]
InvalidationMode = Literal["partial", "full_recompute"]


StepSpec = CheckpointStepSpec


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


_OPERATION_LOOP_CONTRACT = require_checkpoint_graph_contract(OPERATION_LOOP_SKILL_CODE, 1)
OPERATION_LOOP_GRAPH = OperationLoopGraph(
    skill_code=_OPERATION_LOOP_CONTRACT.skill_code,
    version=_OPERATION_LOOP_CONTRACT.graph_version,
    steps=_OPERATION_LOOP_CONTRACT.steps,
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
        step.key for step in graph.steps if step.consumes_constraints.intersection(normalized)
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
        changed_constraints=tuple(path.value for path in ConstraintPath if path in normalized),
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
            InvalidationReason(step_key, "fallback", (fallback_reason,)) for step_key in step_order
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
