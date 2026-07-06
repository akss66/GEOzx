"""运营大脑与专家团 API schema。"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AgentCode,
    AgentGroup,
    AgentInvocationStatus,
    AutomationLevel,
    BrainTaskStatus,
    BrainTaskType,
    DeliverableAcceptanceStatus,
    DeliverableStatus,
    DeliverableType,
    Platform,
    RerunScope,
)


class DraftBrainTaskRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    project_id: int | None = None
    account_group_id: int | None = None
    platforms: list[Platform] | None = None
    account_ids: list[int] = Field(default_factory=list)


class TaskBriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    goal: str
    project_id: int | None
    project_name: str | None
    account_group_id: int | None
    account_group_name: str | None
    platforms: list[Platform]
    account_ids: list[int]
    cycle: str
    budget: Decimal | None
    content_goal: str
    risk_constraints: list[str]
    expected_outputs: list[str]
    confirmation_actions: list[str]


class OrchestrationPlanStep(BaseModel):
    id: str
    agent_code: AgentCode
    agent_name: str
    phase: str
    intent: str
    status: str
    depends_on: list[str] = []
    expected_output: str
    risk_level: str
    execution_kind: str = "analysis"
    human_gate: bool = False
    tool_codes: list[str] = []


class OrchestrationPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    summary: str
    steps: list[OrchestrationPlanStep]
    quality_gates: list[str]
    estimated_cost: Decimal
    requires_human_confirmation: bool


class BrainTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    content_item_id: int | None
    title: str
    type: BrainTaskType
    status: BrainTaskStatus
    brief: TaskBriefOut
    plan: OrchestrationPlanOut
    progress: int
    current_focus: str
    risk_count: int
    context_closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentInvocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    agent_code: AgentCode
    agent_name: str
    status: AgentInvocationStatus
    input_summary: str
    output_summary: str
    model: str
    token_count: int
    cost: Decimal
    failure_reason: str | None
    upstream: list[int]
    started_at: datetime | None
    finished_at: datetime | None


class AgentToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: int
    task_id: int
    invocation_id: int | None
    module: str
    agent_code: str | None
    tool_code: str
    tool_name: str
    status: str
    permission_mode: str
    requires_human_confirmation: bool
    input_summary: str
    output_summary: str
    error: str | None
    latency_ms: int | None
    cost: Decimal
    meta: dict
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApproveToolCallRequest(BaseModel):
    approved: bool
    comment: str | None = Field(default=None, max_length=1000)


class AcceptanceItem(BaseModel):
    label: str
    status: str
    note: str


class AcceptanceHistoryVersion(BaseModel):
    version: int
    status: DeliverableStatus
    note: str
    created_at: str


class DeliverableAcceptanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    deliverable_id: int | None
    agent_code: AgentCode
    agent_name: str
    deliverable_type: DeliverableType
    title: str
    version: int
    summary: str
    acceptance_items: list[AcceptanceItem]
    history_versions: list[AcceptanceHistoryVersion]
    status: DeliverableAcceptanceStatus
    reviewer_note: str | None
    rerun_scope: RerunScope | None
    brain_rejudge_summary: str | None
    brain_rejudge_basis: list[str]


class AcceptDeliverableRequest(BaseModel):
    acceptance_id: int
    reviewer_note: str | None = None


class RerunDeliverableRequest(BaseModel):
    acceptance_id: int
    reason: str = Field(min_length=1, max_length=2000)
    rerun_scope: RerunScope = RerunScope.CURRENT_AGENT
    ask_brain_rejudge: bool = True


class RejudgeDeliverableRequest(BaseModel):
    acceptance_id: int


class CloseMemoryOut(BaseModel):
    task_id: int
    closed: bool
    context_closed_at: datetime


class AgentCurrentTaskOut(BaseModel):
    task_id: int
    title: str
    project_name: str
    account_group_name: str
    platforms: list[Platform]
    progress: int
    risk_level: str
    blockers: list[str]
    next_action: str
    output_summary: str


class AgentToolCallSummaryItem(BaseModel):
    id: int
    task_id: int
    tool_code: str
    tool_name: str
    status: str
    permission_mode: str
    requires_human_confirmation: bool
    input_summary: str
    output_summary: str
    error: str | None
    created_at: datetime


class AgentToolCallSummaryOut(BaseModel):
    total_calls: int
    pending_approvals: int
    failed_calls: int
    recent_calls: list[AgentToolCallSummaryItem]


class AgentProfileOut(BaseModel):
    code: AgentCode
    name: str
    group: AgentGroup
    one_liner: str
    model: str
    fallback_model: str | None
    automation_level: AutomationLevel
    tools: list[str]
    typical_tasks: list[str]
    standard_outputs: list[DeliverableType]
    current_task: AgentCurrentTaskOut | None
    tool_summary: AgentToolCallSummaryOut


class UpdateAgentConfigRequest(BaseModel):
    primary_model: str | None = Field(default=None, max_length=128)
    fallback_model: str | None = Field(default=None, max_length=128)
    automation_level: AutomationLevel | None = None


class InvokeAgentRequest(BaseModel):
    task_id: int | None = None
    prompt: str = Field(min_length=1, max_length=4000)


class InvokeAgentOut(BaseModel):
    invocation: AgentInvocationOut
    message: str
