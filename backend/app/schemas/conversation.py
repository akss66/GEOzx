"""Typed contracts for routing one main-Agent conversation turn."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


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
        if self.mode is TurnExecutionMode.SKILL and not self.skill_code:
            raise ValueError("SKILL routes require skill_code")
        if self.mode is TurnExecutionMode.CLARIFY:
            if not self.missing_field:
                raise ValueError("CLARIFY routes require missing_field")
            if not self.clarifying_question:
                raise ValueError("CLARIFY routes require clarifying_question")
        return self
