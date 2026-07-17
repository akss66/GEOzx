from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ClientStatus


class CreateClientRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class UpdateClientRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: ClientStatus | None = None


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: ClientStatus
    created_at: datetime
