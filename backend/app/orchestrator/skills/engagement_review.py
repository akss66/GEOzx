"""Evidence-bound engagement review Skill."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.skills import SkillDefinition


class EngagementReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=30, ge=1, le=90)
    content_item_ids: list[int] = Field(default_factory=list, max_length=50)
    response_scope: Literal["all", "questions", "negative_feedback"] = "all"


class EngagementReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["engagement_review"] = "engagement_review"
    account_id: int = Field(gt=0)
    period: dict[str, Any]
    status: Literal["ready", "needs_input"]
    common_questions: list[str] = Field(default_factory=list)
    sentiment: dict[str, Any] = Field(default_factory=dict)
    response_guidelines: list[str] = Field(default_factory=list)
    content_opportunities: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    participating_experts: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


ENGAGEMENT_REVIEW_SKILL = SkillDefinition(
    code="engagement_review",
    version=1,
    name="互动复盘",
    description="由客服反馈专家分析当前账号的真实互动样本，形成回复规范和内容机会；不会自动回复评论。",
    supported_platforms=frozenset({"douyin", "xiaohongshu", "shipinhao"}),
    input_model=EngagementReviewInput,
    output_model=EngagementReviewReport,
    expert_codes=("08-customer-service",),
    expert_stages=(("08-customer-service",),),
    tool_codes=("account.engagement_context",),
    critic_policy="required",
    risk_level="low",
    approval_policy="none",
    artifact_type="engagement_review",
)


__all__ = [
    "ENGAGEMENT_REVIEW_SKILL",
    "EngagementReviewInput",
    "EngagementReviewReport",
]
