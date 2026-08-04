"""Contract for planning one evidence-backed operating iteration."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.skills import SkillDefinition


class OperationIterationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_review_artifact_id: int = Field(gt=0)
    cycle_days: int = Field(default=7, ge=1, le=30)
    topic_count: int | None = Field(default=None, ge=1, le=50)
    script_duration_seconds: int | None = Field(default=None, ge=10, le=600)
    positioning_artifact_id: int | None = Field(default=None, gt=0)


class OperationIterationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["operation_execution_plan"] = "operation_execution_plan"
    account_id: int = Field(gt=0)
    cycle_days: int = Field(ge=1, le=30)
    source_artifacts: list[dict[str, Any]] = Field(min_length=1)
    child_skill_graph: list[dict[str, Any]] = Field(min_length=1)
    dependencies: list[dict[str, Any]] = Field(min_length=1)
    approval_points: list[dict[str, Any]] = Field(min_length=1)
    participating_experts: list[str] = Field(default_factory=list)


OPERATION_ITERATION_SKILL = SkillDefinition(
    code="operation_iteration",
    version=1,
    name="运营迭代",
    description="基于已确认复盘成果编排下一周期的选题、脚本、视觉、排期和发布准备，不代替子专家生成专业结论。",
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
    "OperationIterationInput",
    "OperationIterationPlan",
]
