"""Business-facing artifact contracts shared by chat and the Artifact Center."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ArtifactStatus = Literal[
    "draft",
    "ready_for_review",
    "accepted",
    "revision_requested",
    "superseded",
]
ScriptPresentationFormat = Literal[
    "spoken",
    "storyboard",
    "product_video",
    "image_post",
    "live_flow",
]
DeliverableActionCode = Literal[
    "create_optimization_plan",
    "generate_production_briefs",
    "create_shoot_task",
    "add_to_schedule",
    "prepare_manual_publish",
    "record_publish_result",
    "generate_next_iteration",
    "request_revision",
    "export",
]


class ArtifactPresentationOut(BaseModel):
    type_label: str
    completion_label: str
    status_label: str
    detail_action_label: str


class DeliverableActionOut(BaseModel):
    code: DeliverableActionCode
    label: str
    requires_confirmation: bool = False


class ArtifactSection(BaseModel):
    key: str
    title: str
    content: str | int | float | bool | list[Any] | dict[str, Any]


class EvidenceRef(BaseModel):
    kind: str
    id: int
    label: str


class ArtifactEvidenceGroup(BaseModel):
    kind: str
    label: str
    count: int = Field(ge=1)
    metric_count: int = Field(ge=0)
    period: str | None = None


class ArtifactEvidenceSummary(BaseModel):
    total: int = Field(ge=0)
    metric_count: int = Field(default=0, ge=0)
    groups: list[ArtifactEvidenceGroup] = Field(default_factory=list)


class ArtifactQuality(BaseModel):
    score: float = Field(ge=0, le=100)
    passed: bool
    issues: list[str] = Field(default_factory=list)


class ArtifactOut(BaseModel):
    id: int
    account_id: int
    thread_id: int | None
    turn_id: int | None
    run_id: int | None
    skill_run_id: int | None
    task_id: int | None
    artifact_type: str
    presentation_format: ScriptPresentationFormat | None = None
    presentation: ArtifactPresentationOut
    next_actions: list[DeliverableActionOut]
    title: str
    version: int
    status: ArtifactStatus
    summary: str
    sections: list[ArtifactSection]
    evidence_refs: list[EvidenceRef]
    evidence_summary: ArtifactEvidenceSummary
    quality: ArtifactQuality | None
    created_at: datetime


class ArtifactPagination(BaseModel):
    page: int
    page_size: int
    total: int
    pages: int


class ArtifactPageOut(BaseModel):
    data: list[ArtifactOut]
    pagination: ArtifactPagination


class ArtifactRevisionRequest(BaseModel):
    artifact_id: int
    payload: dict[str, Any]
    note: str | None = Field(default=None, max_length=1000)


class ArtifactAcceptanceRequest(BaseModel):
    artifact_id: int
