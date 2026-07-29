"""Stable contract for the bounded one-click account-inspection Skill."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.skills import SkillDefinition


class AccountInspectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=30, ge=1, le=90)


class AccountInspectionMetric(BaseModel):
    name: str
    value: int | float
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class AccountInspectionCriticOutcome(BaseModel):
    passed: bool
    score: int = Field(ge=0, le=100)
    iterations: int = Field(ge=1, le=3)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class AccountInspectionReport(BaseModel):
    """User-facing report. Technical traces remain in their durable ledgers."""

    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["account_inspection_report"] = "account_inspection_report"
    account_id: int = Field(gt=0)
    period: dict[str, Any]
    data_sufficiency: Literal["insufficient", "partial", "sufficient"]
    missing_data: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    key_metrics: list[AccountInspectionMetric] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    next_action: str = Field(min_length=1)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    participating_experts: list[str] = Field(default_factory=list)
    critic: AccountInspectionCriticOutcome


ACCOUNT_INSPECTION_SKILL = SkillDefinition(
    code="account_inspection",
    version=2,
    name="一键账号体检",
    description="读取所选账号证据，由运营、定位和内容专家完成有质量门的账号体检。",
    supported_platforms=frozenset({"douyin", "xiaohongshu", "shipinhao"}),
    input_model=AccountInspectionInput,
    output_model=AccountInspectionReport,
    expert_codes=("01-positioning", "02-content-director", "06-operator"),
    tool_codes=("account.profile", "account.data_context"),
    risk_level="low",
    approval_policy="none",
    artifact_type="account_inspection_report",
)


__all__ = [
    "ACCOUNT_INSPECTION_SKILL",
    "AccountInspectionCriticOutcome",
    "AccountInspectionInput",
    "AccountInspectionMetric",
    "AccountInspectionReport",
]
