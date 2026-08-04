"""Public contracts for durable turn interrupts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TurnInterruptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    thread_id: int
    turn_id: int
    run_id: int
    kind: Literal["clarification", "approval", "manual_pause"]
    status: Literal["pending", "resolved", "cancelled", "expired", "superseded"]
    public_message: str
    action_label: str | None = None
    response_schema: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(ge=1)
    resolved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

