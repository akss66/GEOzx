"""编排相关 schema：内容创建、看板视图、质量门审批。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    AgentTaskStatus,
    ComplianceRisk,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
    GateStatus,
    GateType,
)


class CreateContentItemRequest(BaseModel):
    project_id: int
    title: str
    account_id: int | None = None


class ApproveGateRequest(BaseModel):
    approved: bool
    comment: str | None = None


class RerunStageRequest(BaseModel):
    stage: ContentStage


class ContentItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    account_id: int | None
    title: str
    current_stage: ContentStage
    status: ContentStatus
    created_at: datetime


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
    compliance: list[ComplianceCheckOut] = []
