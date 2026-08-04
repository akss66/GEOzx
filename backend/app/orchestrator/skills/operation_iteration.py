"""Contract for planning one evidence-backed operating iteration."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.skills import SkillDefinition


class OperationConstraintTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["content_item_indexes"] = "content_item_indexes"
    item_indexes: list[Annotated[int, Field(ge=1)]] = Field(min_length=1)


class OperationIterationConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraint_type: Literal["OFFER_TERMS"]
    raw_requirement: str = Field(min_length=1, max_length=500)
    target_scope: OperationConstraintTarget


class OperationIterationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_review_artifact_id: int | None = Field(default=None, gt=0)
    cycle_days: int = Field(default=7, ge=1, le=30)
    topic_count: int = Field(default=5, ge=1, le=50)
    script_duration_seconds: int | None = Field(default=None, ge=10, le=600)
    positioning_artifact_id: int | None = Field(default=None, gt=0)
    constraints: list[OperationIterationConstraint] = Field(default_factory=list)


class OperationIterationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["operation_execution_plan"] = "operation_execution_plan"
    account_id: int = Field(gt=0)
    cycle_days: int = Field(ge=1, le=30)
    source_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    child_skill_graph: list[dict[str, Any]] = Field(min_length=1)
    dependencies: list[dict[str, Any]] = Field(min_length=1)
    approval_points: list[dict[str, Any]] = Field(min_length=1)
    participating_experts: list[str] = Field(default_factory=list)
    required_children_completed: bool = False
    interrupt: dict[str, Any] | None = None


OPERATION_ITERATION_SKILL = SkillDefinition(
    code="operation_iteration",
    version=1,
    name="运营迭代",
    description=(
        "基于当前账号的可审计数据与对标证据，或已确认复盘成果，编排下一周期的选题、"
        "脚本、视觉、排期和发布准备，不代替子专家生成专业结论。"
    ),
    supported_platforms=frozenset({"douyin", "xiaohongshu", "shipinhao"}),
    input_model=OperationIterationInput,
    output_model=OperationIterationPlan,
    expert_codes=(),
    expert_stages=(),
    tool_codes=(),
    critic_policy="none",
    risk_level="medium",
    approval_policy="none",
    artifact_type="operation_execution_plan",
)


__all__ = [
    "OPERATION_ITERATION_SKILL",
    "OperationIterationConstraint",
    "OperationIterationInput",
    "OperationIterationPlan",
]
