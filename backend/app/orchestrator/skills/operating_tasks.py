"""Frozen contracts for the first account-operations Skill loop."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.orchestrator.operation_quality import ArtifactQuality
from app.orchestrator.skills.content_calendar_planning import CalendarSlot
from app.orchestrator.skills.visual_brief_generation import VisualProductionItem
from app.schemas.artifacts import ScriptPresentationFormat
from app.schemas.skills import SkillDefinition

_PLATFORMS = frozenset({"douyin", "xiaohongshu", "shipinhao"})


class TopicPlanningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=7, ge=1, le=30)
    topic_count: int = Field(default=5, ge=1, le=20)


class TopicPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str = ""
    title: str = ""
    angle: str = ""
    format: str = ""


class TopicPlanningReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["topic_plan"] = "topic_plan"
    account_id: int = Field(gt=0)
    period: str = Field(min_length=1)
    theme: str = Field(min_length=1)
    topics: list[TopicPlanItem] = Field(default_factory=list)
    posting_notes: list[str] = Field(default_factory=list)
    quality: ArtifactQuality
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    participating_experts: list[str] = Field(default_factory=list)


class FilmingScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_id: str = Field(min_length=1)
    topic_id: str = ""
    title: str = ""
    hook: str = ""
    voiceover: str = ""
    shot_list: list[str] = Field(default_factory=list)
    duration_seconds: int = Field(gt=0)
    cta: str = ""
    constraints_hit: list[str] = Field(default_factory=list)


class ScriptGenerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: int = Field(default=60, ge=10, le=600)
    presentation_format: ScriptPresentationFormat = "storyboard"


class ScriptGenerationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["video_script"] = "video_script"
    account_id: int = Field(gt=0)
    title: str = ""
    hook: str = ""
    scenes: list[str] = Field(default_factory=list)
    duration_seconds: int = Field(gt=0)
    presentation_format: ScriptPresentationFormat = "storyboard"
    bgm_suggestion: str | None = None
    scripts: list[FilmingScript] = Field(default_factory=list)
    quality: ArtifactQuality
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    participating_experts: list[str] = Field(default_factory=list)


class PublishingPreparationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_item_id: int | None = Field(default=None, gt=0)


class OperationArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: int = Field(gt=0)
    artifact_type: str = Field(min_length=1)
    version: int = Field(gt=0)


class DataEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    id: int = Field(gt=0)
    data_domains: list[str] = Field(default_factory=list)


class ArtifactEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: int = Field(gt=0)
    artifact_type: str = Field(min_length=1)
    version: int = Field(gt=0)


class OperationQualityBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: ArtifactQuality
    scripts: ArtifactQuality
    visuals: ArtifactQuality
    calendar: ArtifactQuality


class PublicNextStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Literal["start_filming", "confirm_manual_schedule"]
    label: str = Field(min_length=1)


class WeeklyOperationPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    source_artifacts: list[OperationArtifactRef] = Field(min_length=4)
    evidence_refs: list[DataEvidenceRef | ArtifactEvidenceRef] = Field(min_length=1)
    topics: list[TopicPlanItem] = Field(min_length=5, max_length=5)
    scripts: list[FilmingScript] = Field(min_length=5, max_length=5)
    visuals: list[VisualProductionItem] = Field(min_length=5, max_length=5)
    calendar_slots: list[CalendarSlot] = Field(min_length=7, max_length=7)
    quality: OperationQualityBundle
    participating_experts: list[str] = Field(min_length=1)
    manual_publish_checklist: list[str] = Field(min_length=1)
    next_steps: list[PublicNextStep] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_complete_package(self) -> WeeklyOperationPackage:
        if any(
            result.status != "passed"
            for result in (
                self.quality.topics,
                self.quality.scripts,
                self.quality.visuals,
                self.quality.calendar,
            )
        ):
            raise ValueError("weekly operation package quality must pass")
        topic_ids = [item.topic_id for item in self.topics]
        if (
            len(set(topic_ids)) != 5
            or any(
                not (item.topic_id and item.title and item.angle and item.format)
                for item in self.topics
            )
        ):
            raise ValueError("weekly operation topics must be complete and unique")
        script_ids = [item.script_id for item in self.scripts]
        script_topics = [item.topic_id for item in self.scripts]
        if (
            len(set(script_ids)) != 5
            or len(set(script_topics)) != 5
            or set(script_topics) != set(topic_ids)
            or any(
                not (
                    item.script_id
                    and item.topic_id
                    and item.title
                    and item.hook
                    and item.voiceover
                    and item.shot_list
                    and item.cta
                )
                for item in self.scripts
            )
        ):
            raise ValueError("weekly operation scripts must be complete and mapped")
        script_topics_by_id = {item.script_id: item.topic_id for item in self.scripts}
        visual_ids = [item.visual_id for item in self.visuals]
        if any(
            not (
                item.script_id
                and item.topic_id
                and item.cover_copy
                and item.composition
                and item.shot_list
                and item.asset_checklist
                and item.platform_constraints
            )
            for item in self.visuals
        ) or (
            len(set(visual_ids)) != 5
            or {item.script_id for item in self.visuals} != set(script_ids)
            or any(
                script_topics_by_id.get(item.script_id) != item.topic_id
                for item in self.visuals
            )
        ):
            raise ValueError("weekly operation visuals must be complete")
        publish_slots = [
            item for item in self.calendar_slots if item.slot_type == "publish"
        ]
        buffer_slots = [
            item for item in self.calendar_slots if item.slot_type == "review_buffer"
        ]
        slot_ids = [item.slot_id for item in self.calendar_slots]
        slot_dates = [item.date for item in self.calendar_slots]
        if (
            len(publish_slots) != 5
            or len(buffer_slots) != 2
            or len(set(slot_ids)) != 7
            or len(set(slot_dates)) != 7
            or any(
                (slot_dates[index] - slot_dates[index - 1]).days != 1
                for index in range(1, len(slot_dates))
            )
            or {item.script_id for item in publish_slots} != set(script_ids)
            or any(
                item.scheduled_at is None
                or item.readiness != "ready"
                or item.topic_id != script_topics_by_id.get(item.script_id or "")
                for item in publish_slots
            )
            or any(
                item.script_id is not None
                or item.topic_id is not None
                or item.scheduled_at is not None
                or item.readiness != "buffer"
                for item in buffer_slots
            )
        ):
            raise ValueError("weekly operation calendar must map five publish slots")
        if {
            item.code for item in self.next_steps
        } != {"start_filming", "confirm_manual_schedule"} or len(self.next_steps) != 2:
            raise ValueError("weekly operation next steps must expose both public actions")
        if any(
            not item.strip()
            for item in (*self.manual_publish_checklist, *self.participating_experts)
        ):
            raise ValueError("weekly operation checklist and experts must be non-empty")
        return self


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
    package: WeeklyOperationPackage | None = None
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
    "ArtifactEvidenceRef",
    "DataEvidenceRef",
    "OperationQualityBundle",
    "PublicNextStep",
    "PublishingPreparationInput",
    "PublishingPreparationReport",
    "ScriptGenerationInput",
    "ScriptGenerationReport",
    "TopicPlanningInput",
    "TopicPlanItem",
    "TopicPlanningReport",
    "FilmingScript",
    "OperationArtifactRef",
    "WeeklyOperationPackage",
]
