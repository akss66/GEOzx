"""Strict contract for visual production briefs derived from confirmed artifacts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.skills import SkillDefinition


class VisualBriefGenerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_artifact_ids: list[int] = Field(min_length=1, max_length=20)


class VisualBriefGenerationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["visual_brief"] = "visual_brief"
    account_id: int = Field(gt=0)
    source_artifact_ids: list[int] = Field(min_length=1)
    cover_copy: str = Field(min_length=1)
    composition: str = Field(min_length=1)
    shot_list: list[str] = Field(min_length=1)
    asset_checklist: list[str] = Field(min_length=1)
    platform_constraints: list[str] = Field(min_length=1)
    evidence_refs: list[dict[str, Any]] = Field(min_length=1)
    participating_experts: list[str] = Field(min_length=1)


VISUAL_BRIEF_GENERATION_SKILL = SkillDefinition(
    code="visual_brief_generation",
    version=1,
    name="视觉 Brief",
    description="基于已确认选题或脚本，由视觉专家形成封面、构图、镜头和素材清单。",
    supported_platforms=frozenset({"douyin", "xiaohongshu", "shipinhao"}),
    input_model=VisualBriefGenerationInput,
    output_model=VisualBriefGenerationReport,
    expert_codes=("03-art-director", "04-video-creator"),
    expert_stages=(("03-art-director", "04-video-creator"),),
    tool_codes=("account.profile",),
    critic_policy="required",
    risk_level="low",
    approval_policy="none",
    artifact_type="visual_brief",
)


__all__ = [
    "VISUAL_BRIEF_GENERATION_SKILL",
    "VisualBriefGenerationInput",
    "VisualBriefGenerationReport",
]
