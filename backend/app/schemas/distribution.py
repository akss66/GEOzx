"""Matrix distribution schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Platform
from app.schemas.orchestrator import PublishPackageOut

MatrixPlanStatus = Literal[
    "draft",
    "pending_approval",
    "queued",
    "running",
    "waiting_manual",
    "completed",
    "failed",
    "cancelled",
]


class CreateMatrixDistributionPlanRequest(BaseModel):
    platforms: list[Platform] = Field(default_factory=lambda: [Platform.DOUYIN], min_length=1)
    account_ids: list[int] = Field(min_length=1)
    material_ids: list[int] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=2000)
    topics: list[str] = Field(default_factory=list, max_length=10)
    scheduled_at: datetime | None = None
    content_item_id: int | None = None
    cover_material_id: int | None = None


class MatrixDistributionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_id: int
    platform: Platform
    account_id: int
    material_id: int
    status: str
    tool_call_id: int | None
    publish_package: PublishPackageOut
    retry_count: int
    next_retry_at: datetime | None
    error: str | None


class MatrixDistributionPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    content_item_id: int | None
    title: str
    body: str
    platforms: list[Platform]
    account_ids: list[int]
    material_ids: list[int]
    topics: list[str]
    scheduled_at: datetime | None
    status: MatrixPlanStatus | str
    items: list[MatrixDistributionItemOut]
    created_at: datetime
