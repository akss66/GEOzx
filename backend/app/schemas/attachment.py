"""Public and runtime-safe conversation attachment contracts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class AttachmentContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(gt=0)
    filename: str
    mime_type: str
    parsed_context: dict[str, JsonValue] = Field(default_factory=dict)


class ConversationAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    thread_id: int
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    scan_status: str
    parse_status: str
    parsed_context: dict
    created_at: datetime
