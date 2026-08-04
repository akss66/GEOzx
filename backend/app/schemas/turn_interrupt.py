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


class ResolveTurnInterruptRequest(BaseModel):
    expected_version: int = Field(ge=1)
    resolution: dict[str, Any]


class ResolveTurnInterruptOut(BaseModel):
    interrupt: TurnInterruptOut
    run_id: int
    dispatch_deferred: bool = False
    dispatch_message: str | None = None


class StopConversationTurnRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class StopConversationTurnOut(BaseModel):
    thread_id: int
    turn_id: int
    run_id: int
    stopped: bool = True
    dispatch_deferred: bool = False
