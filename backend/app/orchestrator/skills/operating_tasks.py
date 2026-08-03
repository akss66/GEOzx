"""Frozen contracts for the first account-operations Skill loop."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.artifacts import ScriptPresentationFormat
from app.schemas.skills import SkillDefinition

_PLATFORMS = frozenset({"douyin", "xiaohongshu", "shipinhao"})


class TopicPlanningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=7, ge=1, le=30)
    topic_count: int = Field(default=5, ge=1, le=20)


class TopicPlanningReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["topic_plan"] = "topic_plan"
    account_id: int = Field(gt=0)
    period: str = Field(min_length=1)
    theme: str = Field(min_length=1)
    topics: list[dict[str, Any]] = Field(min_length=1)
    posting_notes: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    participating_experts: list[str] = Field(default_factory=list)


class ScriptGenerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: int = Field(default=60, ge=10, le=600)
    presentation_format: ScriptPresentationFormat = "storyboard"


class ScriptGenerationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["video_script"] = "video_script"
    account_id: int = Field(gt=0)
    title: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    scenes: list[str] = Field(min_length=3)
    duration_seconds: int = Field(gt=0)
    presentation_format: ScriptPresentationFormat = "storyboard"
    bgm_suggestion: str | None = None
    participating_experts: list[str] = Field(default_factory=list)


class PublishingPreparationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_item_id: int | None = Field(default=None, gt=0)


class PublishingPreparationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["publish_calendar"] = "publish_calendar"
    account_id: int = Field(gt=0)
    platform: str = Field(min_length=1)
    readiness: Literal["ready", "needs_input", "blocked"]
    period: str = Field(min_length=1)
    items: list[dict[str, Any]] = Field(min_length=1)
    operating_notes: list[str] = Field(default_factory=list)
    approval_required: bool = True
    participating_experts: list[str] = Field(default_factory=list)


class PerformanceReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=30, ge=1, le=90)


class PerformanceReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["review_report"] = "review_report"
    account_id: int = Field(gt=0)
    period: dict[str, Any]
    data_sufficiency: Literal["insufficient", "partial", "sufficient"]
    summary: str = Field(min_length=1)
    key_metrics: dict[str, Any] = Field(default_factory=dict)
    highlights: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    optimization_suggestions: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    participating_experts: list[str] = Field(default_factory=list)


TOPIC_PLANNING_SKILL = SkillDefinition(
    code="topic_planning",
    version=1,
    name="选题策划",
    description="结合所选账号定位和数据，由内容策略专家生成可执行选题计划。",
    supported_platforms=_PLATFORMS,
    input_model=TopicPlanningInput,
    output_model=TopicPlanningReport,
    expert_codes=("02-content-director",),
    expert_stages=(("02-content-director",),),
    tool_codes=("account.profile", "account.data_context"),
    critic_policy="none",
    risk_level="low",
    approval_policy="none",
    artifact_type="topic_plan",
)

SCRIPT_GENERATION_SKILL = SkillDefinition(
    code="script_generation",
    version=1,
    name="脚本生成",
    description="由内容策略专家根据当前需求生成结构化短视频脚本。",
    supported_platforms=_PLATFORMS,
    input_model=ScriptGenerationInput,
    output_model=ScriptGenerationReport,
    expert_codes=("02-content-director",),
    expert_stages=(("02-content-director",),),
    tool_codes=("account.profile",),
    critic_policy="none",
    risk_level="low",
    approval_policy="none",
    artifact_type="video_script",
)

PUBLISHING_PREPARATION_SKILL = SkillDefinition(
    code="publishing_preparation",
    version=1,
    name="发布准备",
    description="由账号运营专家检查发布条件并生成发布前清单；不会直接发布。",
    supported_platforms=_PLATFORMS,
    input_model=PublishingPreparationInput,
    output_model=PublishingPreparationReport,
    expert_codes=("06-operator",),
    expert_stages=(("06-operator",),),
    tool_codes=("publish_package_prepare",),
    critic_policy="none",
    risk_level="medium",
    approval_policy="before_finish",
    artifact_type="publish_calendar",
)

PERFORMANCE_REVIEW_SKILL = SkillDefinition(
    code="performance_review",
    version=1,
    name="数据复盘",
    description="读取所选账号数据，由账号运营和内容专家形成复盘结论与优化建议。",
    supported_platforms=_PLATFORMS,
    input_model=PerformanceReviewInput,
    output_model=PerformanceReviewReport,
    expert_codes=("06-operator", "02-content-director"),
    expert_stages=(("06-operator", "02-content-director"),),
    tool_codes=("account.data_context",),
    critic_policy="none",
    risk_level="low",
    approval_policy="none",
    artifact_type="review_report",
)


__all__ = [
    "PERFORMANCE_REVIEW_SKILL",
    "PUBLISHING_PREPARATION_SKILL",
    "SCRIPT_GENERATION_SKILL",
    "TOPIC_PLANNING_SKILL",
    "PerformanceReviewInput",
    "PerformanceReviewReport",
    "PublishingPreparationInput",
    "PublishingPreparationReport",
    "ScriptGenerationInput",
    "ScriptGenerationReport",
    "TopicPlanningInput",
    "TopicPlanningReport",
]
