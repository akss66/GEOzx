"""Typed contract for evidence-grounded account data questions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.skills import SkillDefinition
from app.services.account_metric_analysis import (
    AnalysisFact,
    Answerability,
    BusinessEvidenceRef,
)


class AccountDataAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=2, max_length=1000)
    days: int = Field(default=30, ge=1, le=90)
    comparison: Literal["auto", "previous_period", "none"] = "auto"
    requested_metrics: list[str] = Field(default_factory=list, max_length=12)
    top_n: int = Field(default=5, ge=1, le=20)


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=1000)
    validation_metric: str = Field(min_length=1, max_length=100)
    observation_days: int = Field(ge=1, le=30)


class AccountDataAnalysisCriticOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    score: int = Field(ge=0, le=100)
    iterations: int = Field(ge=1, le=2)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class AccountDataAnalysisAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["account_analysis_answer"] = "account_analysis_answer"
    account_id: int = Field(gt=0)
    question: str = Field(min_length=2, max_length=1000)
    answerability: Answerability
    conclusion: str = Field(min_length=1)
    key_facts: list[AnalysisFact] = Field(default_factory=list)
    interpretation: list[str] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    data_limits: list[str] = Field(default_factory=list)
    next_action: str = Field(min_length=1)
    evidence_refs: list[BusinessEvidenceRef] = Field(default_factory=list)
    participating_experts: list[str] = Field(default_factory=list)
    critic: AccountDataAnalysisCriticOutcome


ACCOUNT_DATA_ANALYSIS_SKILL = SkillDefinition(
    code="account_data_analysis",
    version=1,
    name="账号数据分析",
    description="根据已确认导入的数据回答趋势、对比、异常和作品表现问题",
    supported_platforms=frozenset({"douyin"}),
    input_model=AccountDataAnalysisInput,
    output_model=AccountDataAnalysisAnswer,
    expert_codes=("06-operator",),
    expert_stages=(("06-operator",),),
    tool_codes=("account.metrics_analysis",),
    critic_policy="required",
    risk_level="low",
    approval_policy="none",
    artifact_type="account_analysis_answer",
)


__all__ = [
    "ACCOUNT_DATA_ANALYSIS_SKILL",
    "AccountDataAnalysisAnswer",
    "AccountDataAnalysisCriticOutcome",
    "AccountDataAnalysisInput",
    "Recommendation",
]
