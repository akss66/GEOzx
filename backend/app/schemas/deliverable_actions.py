"""Strict public contracts for explicit deliverable actions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DeliverableActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = False
    assignee_id: int | None = Field(default=None, gt=0)
    due_at: datetime | None = None
    scheduled_at: datetime | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=1000)
    payload: dict | None = None


class CreateShootTaskActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = False
    assignee_id: int | None = Field(default=None, gt=0)
    due_at: datetime | None = None
    note: str | None = Field(default=None, max_length=1000)


class AddToScheduleActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = False
    scheduled_at: datetime
    timezone: str = Field(min_length=1, max_length=64)


class RequestRevisionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=1000)
    payload: dict

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("note must not be blank")
        return normalized


class GenerateNextIterationActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeliverableActionResourceOut(BaseModel):
    type: Literal["shoot_task", "schedule_entry", "conversation_turn", "artifact"]
    id: int = Field(gt=0)


class DeliverableActionExecutionOut(BaseModel):
    execution_id: int = Field(gt=0)
    artifact_id: int = Field(gt=0)
    artifact_version: int = Field(gt=0)
    action_code: str
    status: Literal["succeeded", "queued", "pending_confirmation", "failed"]
    resource: DeliverableActionResourceOut | None = None
    result: dict = Field(default_factory=dict)
    replayed: bool = False
