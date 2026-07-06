from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import MaterialStatus


class MaterialAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    content_item_id: int | None
    deliverable_id: int | None
    kind: str
    provider: str | None
    status: MaterialStatus
    size_bytes: int | None
    file_url: str | None
    error: str | None
    created_at: datetime
