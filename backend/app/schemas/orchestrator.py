"""编排相关 schema：内容创建、看板视图、质量门审批。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    AgentTaskStatus,
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


class BoardOut(BaseModel):
    content_item: ContentItemOut
    tasks: list[AgentTaskOut]
    deliverables: list[DeliverableOut]
    gates: list[GateApprovalOut]
