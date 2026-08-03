"""Strict contract for scheduling confirmed content artifacts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.skills import SkillDefinition


class ContentCalendarPlanningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_artifact_ids: list[int] = Field(min_length=1, max_length=50)
    days: int = Field(default=7, ge=1, le=90)


class ContentCalendarPlanningReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["content_calendar"] = "content_calendar"
    account_id: int = Field(gt=0)
    source_artifact_ids: list[int] = Field(min_length=1)
    days: int = Field(ge=1, le=90)
    items: list[dict[str, Any]] = Field(min_length=1)
    evidence_refs: list[dict[str, Any]] = Field(min_length=1)
    participating_experts: list[str] = Field(min_length=1)


CONTENT_CALENDAR_PLANNING_SKILL = SkillDefinition(
    code="content_calendar_planning",
    version=1,
    name="内容排期",
    description="把已确认选题、脚本或视觉成果安排为含负责人、就绪状态和依赖的内容日历。",
    supported_platforms=frozenset({"douyin", "xiaohongshu", "shipinhao"}),
    input_model=ContentCalendarPlanningInput,
    output_model=ContentCalendarPlanningReport,
    expert_codes=("06-operator",),
    expert_stages=(("06-operator",),),
    tool_codes=("account.profile",),
    critic_policy="required",
    risk_level="low",
    approval_policy="none",
    artifact_type="content_calendar",
)


__all__ = [
    "CONTENT_CALENDAR_PLANNING_SKILL",
    "ContentCalendarPlanningInput",
    "ContentCalendarPlanningReport",
]
