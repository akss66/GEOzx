"""Strict contract for the account-positioning business Skill."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.skills import SkillDefinition


class AccountPositioningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_goal: str | None = Field(default=None, max_length=500)
    target_audience: str | None = Field(default=None, max_length=500)
    differentiation_constraints: list[str] = Field(default_factory=list, max_length=20)


class AccountPositioningReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["account_positioning"] = "account_positioning"
    account_id: int = Field(gt=0)
    positioning_statement: str = Field(min_length=1)
    audience: list[str] = Field(min_length=1)
    content_pillars: list[str] = Field(min_length=1)
    tone: str = Field(min_length=1)
    boundaries: list[str] = Field(min_length=1)
    evidence_refs: list[dict[str, Any]] = Field(min_length=1)
    participating_experts: list[str] = Field(min_length=1)


ACCOUNT_POSITIONING_SKILL = SkillDefinition(
    code="account_positioning",
    version=1,
    name="账号定位",
    description="由账号定位专家基于当前账号证据形成定位、受众、内容支柱和业务边界。",
    supported_platforms=frozenset({"douyin", "xiaohongshu", "shipinhao"}),
    input_model=AccountPositioningInput,
    output_model=AccountPositioningReport,
    expert_codes=("01-positioning",),
    expert_stages=(("01-positioning",),),
    tool_codes=("account.profile", "account.data_context"),
    critic_policy="required",
    risk_level="low",
    approval_policy="none",
    artifact_type="account_positioning",
)


__all__ = [
    "ACCOUNT_POSITIONING_SKILL",
    "AccountPositioningInput",
    "AccountPositioningReport",
]
