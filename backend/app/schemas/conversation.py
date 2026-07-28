"""Typed contracts for routing one main-Agent conversation turn."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CreateConversationThreadRequest(BaseModel):
    account_id: int = Field(gt=0)
    title: str = Field(default="", max_length=300)


class CreateConversationTurnRequest(BaseModel):
    client_message_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1)
    requested_skill_code: str | None = Field(default=None, min_length=1, max_length=120)
    execution_preference: Literal["AUTO", "DISCUSS_ONLY", "FORMAL_TASK"] = "AUTO"
    attachment_ids: list[int] = Field(default_factory=list)


class TurnExecutionMode(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    QUERY = "query"
    SKILL = "skill"
    TASK = "task"
    ACTION = "action"


class TurnRouteDecision(BaseModel):
    mode: TurnExecutionMode
    intent: str
    confidence: float = Field(ge=0, le=1)
    reason: str
    skill_code: str | None = None
    requires_account_context: bool = False
    requires_operation_task: bool = False
    missing_field: str | None = None
    clarifying_question: str | None = None

    @model_validator(mode="after")
    def validate_mode_requirements(self) -> TurnRouteDecision:
        if self.mode is not TurnExecutionMode.CLARIFY and (
            self.missing_field or self.clarifying_question
        ):
            raise ValueError("only CLARIFY routes may include clarification fields")

        if self.mode is TurnExecutionMode.SKILL:
            if not self.skill_code:
                raise ValueError("SKILL routes require skill_code")
            if not self.requires_account_context:
                raise ValueError("SKILL routes require account context")
            if not self.requires_operation_task:
                raise ValueError("SKILL routes require an operation task")
        if self.mode is TurnExecutionMode.CLARIFY:
            if not self.missing_field:
                raise ValueError("CLARIFY routes require missing_field")
            if not self.clarifying_question:
                raise ValueError("CLARIFY routes require clarifying_question")
            if self.requires_operation_task:
                raise ValueError("CLARIFY routes cannot require an operation task")
        if self.mode is TurnExecutionMode.ANSWER:
            if self.requires_account_context or self.requires_operation_task or self.skill_code:
                raise ValueError(
                    "ANSWER routes cannot include account, operation, or Skill context"
                )
        if self.mode is TurnExecutionMode.QUERY and self.requires_operation_task:
            raise ValueError("QUERY routes cannot require an operation task")
        if self.mode in {TurnExecutionMode.TASK, TurnExecutionMode.ACTION}:
            if not self.requires_operation_task:
                raise ValueError("TASK and ACTION routes require an operation task")
        return self
