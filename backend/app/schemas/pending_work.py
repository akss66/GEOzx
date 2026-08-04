"""Public contracts for account-scoped operator pending work."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ConversationTurnTarget(BaseModel):
    type: Literal["conversation_turn"] = "conversation_turn"
    thread_id: int
    turn_id: int


class AccountDataTarget(BaseModel):
    type: Literal["account_data"] = "account_data"


class TaskWorkspaceTarget(BaseModel):
    type: Literal["task_workspace"] = "task_workspace"


PendingWorkTarget = Annotated[
    ConversationTurnTarget | AccountDataTarget | TaskWorkspaceTarget,
    Field(discriminator="type"),
]


class PendingWorkItem(BaseModel):
    id: str
    kind: Literal[
        "clarification",
        "approval",
        "shoot_task",
        "manual_publish",
        "account_data",
    ]
    action_label: str
    account_id: int
    thread_id: int | None = None
    turn_id: int | None = None
    due_at: datetime | None = None
    reason: str
    next_step_after_completion: str
    target: PendingWorkTarget


class PendingWorkGroup(BaseModel):
    kind: Literal[
        "clarification",
        "approval",
        "shoot_task",
        "manual_publish",
        "account_data",
    ]
    label: str
    count: int
    items: list[PendingWorkItem]


class PendingWorkResponse(BaseModel):
    account_id: int
    groups: list[PendingWorkGroup]


class PendingWorkCompletion(BaseModel):
    id: str
    kind: Literal["shoot_task", "manual_publish"]
    account_id: int
    completed: Literal[True] = True
    event_id: int
    next_step_after_completion: str
