"""Public response contracts for durable conversation Turn events."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.events import TurnEventPayload


class ConversationTurnEventOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    sequence: int
    type: str
    payload: TurnEventPayload
    thread_id: int
    turn_id: int
    run_id: int | None = None
    skill_run_id: int | None = None
    created_at: datetime


class ConversationTurnEventListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[ConversationTurnEventOut]
