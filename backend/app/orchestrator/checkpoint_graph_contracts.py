"""Single source of truth for checkpointable logical step contracts."""

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
ExecutorOwner = Literal["native_runtime", "child_skill", "manual"]


@dataclass(frozen=True)
class CheckpointStepSpec:
    key: str
    consumes_constraints: frozenset[ConstraintPath]
    consumes_outputs: frozenset[str]
    produces_outputs: frozenset[str]
    reuse_policy: ReusePolicy
    side_effect_level: SideEffectLevel
    input_schema_version: str
    output_schema_version: str
    input_projection_key: str
    freshness_policy_key: str | None
    executor_owner: ExecutorOwner
    executor_boundary_key: str


@dataclass(frozen=True)
class CheckpointGraphContract:
    skill_code: str
    skill_version: int
    graph_version: str
    steps: tuple[CheckpointStepSpec, ...]


class CheckpointGraphContractError(ValueError):
    pass


def _step(
    key: str,
    *,
    constraints: tuple[ConstraintPath, ...] = (),
    inputs: tuple[str, ...] = (),
    outputs: tuple[str, ...] = (),
    reuse_policy: ReusePolicy = "immutable",
    side_effect_level: SideEffectLevel = "none",
    freshness_policy_key: str | None = None,
    executor_boundary_key: str,
) -> CheckpointStepSpec:
    return CheckpointStepSpec(
        key=key,
        consumes_constraints=frozenset(constraints),
        consumes_outputs=frozenset(inputs),
        produces_outputs=frozenset(outputs),
        reuse_policy=reuse_policy,
        side_effect_level=side_effect_level,
        input_schema_version=f"{key}-input/v1",
        output_schema_version=f"{key}-output/v1",
        input_projection_key=f"operation-iteration/{key}/v1",
        freshness_policy_key=freshness_policy_key,
        executor_owner="child_skill",
        executor_boundary_key=executor_boundary_key,
    )


_OPERATION_ITERATION = CheckpointGraphContract(
    skill_code="operation_iteration",
    skill_version=1,
    graph_version="operation-loop/v1",
    steps=(
        _step(
            "read_account_data",
            constraints=(ConstraintPath.DATA_PERIOD,),
            outputs=("account_snapshot",),
            reuse_policy="freshness_bound",
            side_effect_level="read",
            freshness_policy_key="account-snapshot/v1",
            executor_boundary_key="child_skill:account_inspection",
        ),
        _step(
            "benchmark_analysis",
            constraints=(ConstraintPath.SOURCE_ARTIFACTS,),
            inputs=("account_snapshot",),
            outputs=("benchmark_findings",),
            reuse_policy="freshness_bound",
            side_effect_level="read",
            freshness_policy_key="benchmark-evidence/v1",
            executor_boundary_key="child_skill:performance_review",
        ),
        _step(
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
            executor_boundary_key="child_skill:topic_planning",
        ),
        _step(
            "script_generation",
            constraints=(
                ConstraintPath.BRAND_CONSTRAINTS,
                ConstraintPath.PRODUCT_FACTS,
                ConstraintPath.OFFER_TERMS,
                ConstraintPath.SCRIPT_REQUIREMENTS,
            ),
            inputs=("topic_plan",),
            outputs=("scripts",),
            executor_boundary_key="child_skill:script_generation",
        ),
        _step(
            "visual_brief_generation",
            constraints=(
                ConstraintPath.BRAND_CONSTRAINTS,
                ConstraintPath.VISUAL_REQUIREMENTS,
            ),
            inputs=("scripts",),
            outputs=("visual_briefs",),
            executor_boundary_key="child_skill:visual_brief_generation",
        ),
        _step(
            "quality_review",
            constraints=(ConstraintPath.BRAND_CONSTRAINTS,),
            inputs=("topic_plan", "scripts", "visual_briefs"),
            outputs=("quality_result",),
            reuse_policy="never",
            executor_boundary_key="child_skill:quality_review",
        ),
        _step(
            "content_calendar_planning",
            constraints=(ConstraintPath.SCHEDULE_REQUIREMENTS,),
            inputs=("topic_plan", "scripts", "visual_briefs"),
            outputs=("content_calendar",),
            executor_boundary_key="child_skill:content_calendar_planning",
        ),
        _step(
            "publishing_preparation",
            constraints=(
                ConstraintPath.SOURCE_ARTIFACTS,
                ConstraintPath.PUBLISH_PARAMETERS,
            ),
            inputs=("quality_result", "content_calendar"),
            outputs=("publish_package",),
            reuse_policy="never",
            side_effect_level="idempotent_write",
            executor_boundary_key="child_skill:publishing_preparation",
        ),
    ),
)

_CONTRACTS = {
    (
        _OPERATION_ITERATION.skill_code,
        _OPERATION_ITERATION.skill_version,
    ): _OPERATION_ITERATION
}


def get_checkpoint_graph_contract(
    skill_code: str, skill_version: int
) -> CheckpointGraphContract | None:
    return _CONTRACTS.get((skill_code, skill_version))


def require_checkpoint_graph_contract(
    skill_code: str, skill_version: int
) -> CheckpointGraphContract:
    contract = get_checkpoint_graph_contract(skill_code, skill_version)
    if contract is None:
        raise CheckpointGraphContractError(
            f"Checkpoint graph contract is missing: {skill_code}:{skill_version}"
        )
    validate_checkpoint_graph_contract(contract)
    return contract


def validate_checkpoint_graph_contract(contract: CheckpointGraphContract) -> tuple[str, ...]:
    if not contract.skill_code or contract.skill_version < 1 or not contract.graph_version:
        raise CheckpointGraphContractError("Invalid graph identity")
    seen_steps: set[str] = set()
    producers: dict[str, str] = {}
    for step in contract.steps:
        if not step.key or step.key in seen_steps:
            raise CheckpointGraphContractError(f"Invalid or duplicate step: {step.key}")
        seen_steps.add(step.key)
        for field in (
            "input_schema_version",
            "output_schema_version",
            "input_projection_key",
            "executor_boundary_key",
        ):
            if not getattr(step, field):
                raise CheckpointGraphContractError(f"{step.key}.{field} is required")
        if step.reuse_policy == "freshness_bound" and not step.freshness_policy_key:
            raise CheckpointGraphContractError(f"{step.key}.freshness_policy_key is required")
        if step.reuse_policy != "freshness_bound" and step.freshness_policy_key is not None:
            raise CheckpointGraphContractError(f"{step.key}.freshness_policy_key must be absent")
        for output in step.produces_outputs:
            if output in producers:
                raise CheckpointGraphContractError(f"Duplicate output producer: {output}")
            producers[output] = step.key
    for step in contract.steps:
        for consumed in step.consumes_outputs:
            if consumed not in producers:
                raise CheckpointGraphContractError(
                    f"{step.key} consumes unknown output: {consumed}"
                )
    return tuple(step.key for step in contract.steps)


__all__ = [
    "CheckpointGraphContract",
    "CheckpointGraphContractError",
    "CheckpointStepSpec",
    "ConstraintPath",
    "ExecutorOwner",
    "ReusePolicy",
    "SideEffectLevel",
    "get_checkpoint_graph_contract",
    "require_checkpoint_graph_contract",
    "validate_checkpoint_graph_contract",
]
