"""闭环反馈 schema：优化建议列表与状态流转。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ContentStage, OptimizationSuggestionStatus


class OptimizationSuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    content_item_id: int
    content_title: str
    source_deliverable_id: int | None
    target_stage: ContentStage | None
    suggestion: str
    status: OptimizationSuggestionStatus
    note: str | None
    accepted_at: datetime | None
    verified_at: datetime | None
    created_at: datetime


class UpdateOptimizationSuggestionRequest(BaseModel):
    status: OptimizationSuggestionStatus
    note: str | None = None
