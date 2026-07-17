from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title: str
    body: str | None
    path: str | None
    read_at: datetime | None
    created_at: datetime


class SearchResultOut(BaseModel):
    kind: Literal["client", "project", "account"]
    id: int
    title: str
    subtitle: str | None = None
    path: str
    client_id: int | None = None
    project_id: int | None = None
    account_id: int | None = None
