"""Unified approval workbench contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ApprovalKind = Literal["gate", "tool_call", "deliverable"]
ApprovalRisk = Literal["low", "medium", "high", "critical"]


class ApprovalQueueItemOut(BaseModel):
    key: str
    kind: ApprovalKind
    source_id: int
    project_id: int
    project_name: str
    account_id: int | None = None
    account_name: str | None = None
    content_item_id: int | None = None
    content_title: str | None = None
    task_id: int | None = None
    category: str
    title: str
    summary: str
    risk_level: ApprovalRisk
    risk_reasons: list[str] = Field(default_factory=list)
    impact: list[str] = Field(default_factory=list)
    agent_explanation: str
    preview: dict = Field(default_factory=dict)
    can_decide: bool
    created_at: datetime


class ApprovalCountsOut(BaseModel):
    total: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0


class ApprovalWorkspaceOut(BaseModel):
    items: list[ApprovalQueueItemOut]
    counts: ApprovalCountsOut
    can_decide: bool
    generated_at: datetime
