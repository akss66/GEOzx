"""Strict contract for scheduling confirmed content artifacts."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.orchestrator.operation_quality import ArtifactQuality
from app.schemas.skills import SkillDefinition


class ContentCalendarPlanningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_artifact_ids: list[int] = Field(min_length=1, max_length=50)
    days: int = Field(default=7, ge=1, le=90)


class CalendarSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(min_length=1)
    date: date
    slot_type: Literal["publish", "review_buffer"]
    title: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    readiness: Literal["ready", "review", "buffer"]
    topic_id: str | None = None
    script_id: str | None = None
    scheduled_at: datetime | None = None
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"


class ContentCalendarPlanningReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["content_calendar"] = "content_calendar"
    account_id: int = Field(gt=0)
    source_artifact_ids: list[int] = Field(min_length=1)
    days: int = Field(ge=1, le=90)
    items: list[dict[str, Any]] = Field(min_length=1)
    slots: list[CalendarSlot] = Field(min_length=1)
    quality: ArtifactQuality
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
    "CalendarSlot",
]
