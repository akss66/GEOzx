"""Typed contracts for routing one main-Agent conversation turn."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator


class CreateConversationThreadRequest(BaseModel):
    account_id: int = Field(gt=0)
    title: str = Field(default="", max_length=300)


class CreateConversationTurnRequest(BaseModel):
    client_message_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1)
    requested_skill_code: str | None = Field(default=None, min_length=1, max_length=120)
    execution_preference: Literal["AUTO", "DISCUSS_ONLY", "FORMAL_TASK"] = "AUTO"
    attachment_ids: list[int] = Field(default_factory=list)


class ConversationTurnIntentOut(BaseModel):
    """Allowlisted route metadata safe for conversation history."""

    model_config = ConfigDict(extra="forbid")

    mode: str | None = None
    route_source: Literal[
        "deterministic",
        "explicit",
        "model",
        "recovery",
        "system",
    ] = "model"
    skill_code: str | None = None


class ConversationExecutionExpertOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    agent_code: str
    agent_name: str
    status: str
    attempt: int = Field(ge=0)
    duration_ms: int | None = Field(default=None, ge=0)


class ConversationExecutionToolOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    tool_code: str
    tool_name: str
    status: str
    duration_ms: int | None = Field(default=None, ge=0)
    retry_count: int = Field(ge=0)
    requires_confirmation: bool
    side_effect_level: Literal[
        "read",
        "idempotent_write",
        "non_idempotent_write",
    ]


class ConversationExecutionSummaryOut(BaseModel):
    """Strongly typed public execution projection; raw ledgers stay server-side."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["execution_summary"] = "execution_summary"
    turn_id: int | None = Field(default=None, gt=0, exclude_if=lambda value: value is None)
    run_id: int | None = None
    mode: str | None = None
    route_source: Literal[
        "deterministic",
        "explicit",
        "model",
        "recovery",
        "system",
    ]
    skill_code: str | None = None
    skill_version: int | None = None
    skill_run_id: int | None = None
    status: str | None = None
    quality_score: float | None = None
    experts: list[ConversationExecutionExpertOut] = Field(default_factory=list)
    tools: list[ConversationExecutionToolOut] = Field(default_factory=list)
    error_code: str | None = None
    recovery_action: str | None = None
    artifact_ids: list[int] = Field(default_factory=list)
    evidence_ids: list[int] = Field(default_factory=list)


class ConversationProjectionModel(BaseModel):
    """Base for public projections: unknown input keys are discarded."""

    model_config = ConfigDict(extra="ignore")

    turn_id: int | None = Field(default=None, gt=0, exclude_if=lambda value: value is None)


class ConversationProgressStageOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=180)
    status: str = Field(min_length=1, max_length=40)


class ConversationProgressProjectionOut(ConversationProjectionModel):
    type: Literal["progress"] = "progress"
    skill_run_id: int = Field(gt=0)
    stages: list[ConversationProgressStageOut] = Field(default_factory=list, max_length=50)


class ConversationExpertInvocationOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    agent_code: str = Field(min_length=1, max_length=64)
    agent_name: str = Field(min_length=1, max_length=120)
    status: str = Field(min_length=1, max_length=40)
    attempt: int = Field(default=0, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)


class ConversationExpertProjectionOut(ConversationProjectionModel):
    type: Literal["expert"] = "expert"
    invocation: ConversationExpertInvocationOut


class ConversationArtifactProjectionOut(ConversationProjectionModel):
    type: Literal["artifact"] = "artifact"
    artifact_id: int = Field(gt=0)
    artifact_type: str = Field(min_length=1, max_length=120)
    skill_run_id: int = Field(gt=0)
    account_id: int = Field(gt=0)


class ConversationAccountDataProjectionOut(ConversationProjectionModel):
    type: Literal["account_data"] = "account_data"
    account_id: int = Field(gt=0)
    skill_code: str = Field(min_length=1, max_length=120)
    skill_run_id: int = Field(gt=0)


class ConversationExecutionBlockedProjectionOut(ConversationProjectionModel):
    type: Literal["execution_blocked"] = "execution_blocked"
    skill_run_id: int | None = Field(
        default=None,
        gt=0,
        exclude_if=lambda value: value is None,
    )
    code: str = Field(min_length=1, max_length=120)
    artifact_type: str | None = Field(
        default=None,
        max_length=120,
        exclude_if=lambda value: value is None,
    )
    account_id: int | None = Field(
        default=None,
        gt=0,
        exclude_if=lambda value: value is None,
    )
    skill_code: str | None = Field(
        default=None,
        max_length=120,
        exclude_if=lambda value: value is None,
    )
    recovery_action: str | None = Field(
        default=None,
        max_length=500,
        exclude_if=lambda value: value is None,
    )


class ConversationApprovalOut(BaseModel):
    """Allowlisted approval data safe to restore in conversation history."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    task_id: int
    tool_code: str
    tool_name: str
    status: str
    permission_mode: str
    requires_human_confirmation: bool


class ConversationApprovalProjectionOut(ConversationProjectionModel):
    type: Literal["approval"] = "approval"
    approval: ConversationApprovalOut


ConversationProjectionOut: TypeAlias = Annotated[
    ConversationProgressProjectionOut
    | ConversationExpertProjectionOut
    | ConversationExecutionSummaryOut
    | ConversationApprovalProjectionOut
    | ConversationArtifactProjectionOut
    | ConversationAccountDataProjectionOut
    | ConversationExecutionBlockedProjectionOut,
    Field(discriminator="type"),
]
conversation_projection_adapter = TypeAdapter(ConversationProjectionOut)


def sanitize_conversation_projection(value: object) -> dict[str, Any] | None:
    """Return the public allowlist projection or fail closed."""

    try:
        projection = conversation_projection_adapter.validate_python(value)
    except (ValidationError, TypeError, ValueError):
        return None
    return projection.model_dump(mode="json", exclude_none=True)


class ConversationTurnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    thread_id: int
    org_id: int
    created_by_id: int | None
    client_message_id: str | None
    user_input: str
    assistant_response: str | None
    intent: ConversationTurnIntentOut | None
    status: str
    route_ms: int | None = Field(default=None, ge=0)
    first_token_ms: int | None = Field(default=None, ge=0)
    completion_ms: int | None = Field(default=None, ge=0)
    total_ms: int | None = Field(default=None, ge=0)
    model_call_count: int | None = Field(default=None, ge=0)
    projections: list[ConversationProjectionOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ConversationThreadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: int
    created_by_id: int | None
    client_id: int | None
    project_id: int | None
    account_id: int
    title: str
    turns: list[ConversationTurnOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ConversationThreadSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    title: str
    turn_count: int
    last_message: str
    created_at: datetime
    updated_at: datetime


class ConversationThreadListOut(BaseModel):
    data: list[ConversationThreadSummaryOut] = Field(default_factory=list)


class ConversationAgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: int
    requested_by_id: int
    task_id: int | None
    thread_id: int | None
    turn_id: int | None
    client_message_id: str
    status: str
    phase: str
    created_at: datetime
    updated_at: datetime


class TurnSubmissionOut(BaseModel):
    turn: ConversationTurnOut
    run: ConversationAgentRunOut
    task_id: int | None = None
    projections: list[ConversationProjectionOut] = Field(default_factory=list)


class TurnExecutionMode(StrEnum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    QUERY = "query"
    SKILL = "skill"
    TASK = "task"
    ACTION = "action"


class TurnExecutionResult(BaseModel):
    """Persistable result of executing one routed conversation Turn."""

    mode: TurnExecutionMode
    status: str
    response: str
    task_id: int | None = None
    projections: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None


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
