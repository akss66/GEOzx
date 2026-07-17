"""编排相关 schema：内容创建、看板视图、质量门审批。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AgentTaskStatus,
    ComplianceRisk,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
    GateStatus,
    GateType,
    Platform,
)
from app.schemas.brain import AgentToolCallOut
from app.schemas.material import MaterialAssetOut


class CreateContentItemRequest(BaseModel):
    project_id: int
    title: str
    account_id: int | None = None


class ApproveGateRequest(BaseModel):
    approved: bool
    comment: str | None = None


class RerunStageRequest(BaseModel):
    stage: ContentStage


class CreateDeliverableRevisionRequest(BaseModel):
    payload: dict
    note: str | None = Field(default=None, max_length=1000)


class PublishReadinessRequest(BaseModel):
    platform: Platform = Platform.DOUYIN
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=2000)
    topics: list[str] = Field(default_factory=list, max_length=10)
    scheduled_at: datetime | None = None
    material_ids: list[int] = Field(default_factory=list)
    cover_material_id: int | None = None
    visibility: Literal["public", "friends", "private"] = "public"
    allow_comment: bool = True


class PublishCapabilityOut(BaseModel):
    platform: Platform
    content_types: list[Literal["video", "image_text"]]
    supported_fields: list[str]
    execution_mode: Literal["official_api", "manual_checklist", "browser_runner_disabled"]
    permission_status: Literal["oauth_authorized", "pending_review", "prepare_only"]
    browser_runner_enabled: bool = False


class PublishPackageOut(BaseModel):
    platform: Platform
    account_id: int | None
    content_type: Literal["video", "image_text"]
    title: str
    body: str
    topics: list[str]
    scheduled_at: datetime | None
    material_ids: list[int]
    cover_material_id: int | None
    visibility: Literal["public", "friends", "private"]
    allow_comment: bool
    execution_mode: Literal["official_api", "manual_checklist", "browser_runner_disabled"]
    manual_steps: list[str]


class PublishReadinessFinding(BaseModel):
    level: ComplianceRisk
    code: str
    message: str


class PublishReadinessOut(BaseModel):
    content_item_id: int
    platform: Platform
    ready: bool
    risk: ComplianceRisk
    package: PublishPackageOut
    findings: list[PublishReadinessFinding]
    tool_call: AgentToolCallOut


class ContentItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    account_id: int | None
    title: str
    current_stage: ContentStage
    status: ContentStatus
    created_at: datetime


class ContentWorkspaceAccountOut(BaseModel):
    id: int
    nickname: str
    platform: Platform
    auth_status: str


class AgentTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_code: str
    stage: ContentStage
    status: AgentTaskStatus
    output_deliverable_id: int | None


class DeliverableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_code: str
    type: DeliverableType
    version: int
    status: DeliverableStatus
    payload: dict
    created_at: datetime


class GateApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    gate: GateType
    status: GateStatus
    decided_by: int | None
    comment: str | None
    created_at: datetime
    decided_at: datetime | None


class ComplianceCheckOut(BaseModel):
    """合规预检结果（供审批参考）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    risk: ComplianceRisk
    summary: str
    findings: list | None
    created_at: datetime


class PendingGateOut(BaseModel):
    """待审质量门聚合视图（跨内容），供审批列表用。"""

    id: int
    gate: GateType
    status: GateStatus
    content_item_id: int
    content_title: str
    created_at: datetime
    compliance: ComplianceCheckOut | None = None


class BoardOut(BaseModel):
    content_item: ContentItemOut
    tasks: list[AgentTaskOut]
    deliverables: list[DeliverableOut]
    gates: list[GateApprovalOut]
    compliance: list[ComplianceCheckOut] = Field(default_factory=list)


class ContentWorkspaceOut(BoardOut):
    project_name: str
    account: ContentWorkspaceAccountOut | None = None
    materials: list[MaterialAssetOut] = Field(default_factory=list)
    publish_tool_calls: list[AgentToolCallOut] = Field(default_factory=list)
