"""运营大脑与专家团 API schema。"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    KnowledgeCategory,
    Platform,
    RerunScope,
)

ToolPermissionMode = Literal["auto", "confirm", "manual", "disabled"]
IntentKind = Literal["conversation", "clarification", "analysis", "workflow", "action"]
RuntimeAction = Literal[
    "respond",
    "ask_user",
    "dispatch_experts",
    "call_tools",
    "request_decision",
    "request_permission",
    "finish",
]


class RuntimeToolCall(BaseModel):
    tool_code: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    purpose: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=160)


class IntentDecision(BaseModel):
    intent: IntentKind
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=500)
    missing_field: str | None = Field(default=None, max_length=120)
    clarifying_question: str | None = Field(default=None, max_length=500)
    suggested_expert_codes: list[AgentCode] = Field(default_factory=list)
    requires_account_context: bool = False


class DecisionChoice(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=500)
    benefit: str = Field(min_length=1, max_length=300)
    tradeoff: str = Field(min_length=1, max_length=300)
    recommended: bool = False


class DecisionRequest(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=800)
    choices: list[DecisionChoice] = Field(min_length=2, max_length=4)
    allow_custom_input: bool = True
    status: Literal["pending", "selected", "revised"] = "pending"


class RuntimeNextStep(BaseModel):
    action: RuntimeAction
    expert_codes: list[AgentCode] = Field(default_factory=list, max_length=3)
    rationale: str = Field(min_length=1, max_length=800)
    handoff_message: str = Field(min_length=1, max_length=500)
    decision_request: DecisionRequest | None = None
    tool_calls: list[RuntimeToolCall] = Field(default_factory=list, max_length=5)
    purpose: str | None = Field(default=None, max_length=500)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "RuntimeNextStep":
        if self.action == "dispatch_experts" and not self.expert_codes:
            raise ValueError("dispatch_experts requires expert_codes")
        if self.action == "call_tools" and not self.tool_calls:
            raise ValueError("call_tools requires tool_calls")
        if self.action == "request_permission" and not self.tool_calls:
            raise ValueError("request_permission requires tool_calls")
        if self.action == "request_decision" and self.decision_request is None:
            raise ValueError("request_decision requires decision_request")
        if self.action != "request_decision" and self.decision_request is not None:
            raise ValueError("decision_request is only valid for request_decision")
        return self


class DraftBrainTaskRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    project_id: int | None = None
    account_group_id: int | None = None
    platforms: list[Platform] | None = None
    account_ids: list[int] = Field(default_factory=list)


class BrainMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    client_message_id: str | None = Field(default=None, min_length=1, max_length=120)
    task_id: int | None = None
    project_id: int | None = None
    account_id: int | None = None
    platform: Platform = Platform.DOUYIN


class RegenerateBrainMessageRequest(BaseModel):
    client_message_id: str | None = Field(default=None, min_length=1, max_length=120)


class StopBrainGenerationRequest(BaseModel):
    task_id: int | None = None


class StopBrainGenerationOut(BaseModel):
    client_message_id: str
    stop_requested: bool


class DecisionSelectionRequest(BaseModel):
    choice_id: str = Field(min_length=1, max_length=120)


class DecisionRevisionRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)
    request_new_options: bool = False


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
    tool_permissions: dict[str, ToolPermissionMode] = Field(default_factory=dict)
    quality_gates: list[str] = Field(default_factory=list)


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
    runtime_mode: str = "legacy"
    thread_id: str | None = None
    context_closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentInvocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    run_id: int | None = None
    step_key: str | None = None
    attempt: int = 0
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


class RuntimeEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    payload: dict | None
    created_at: datetime


class BrainRuntimeOut(BaseModel):
    task: BrainTaskOut
    thread_id: str | None
    status: str
    timeline: list[RuntimeEventOut]
    invocations: list[AgentInvocationOut]
    tool_calls: list[AgentToolCallOut]
    acceptances: list[DeliverableAcceptanceOut]
    pending_permissions: list[AgentToolCallOut]
    intent: IntentDecision | None = None
    pending_decisions: list[DecisionRequest] = Field(default_factory=list)
    next_actions: list[str]


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


class AgentManagementToolOut(BaseModel):
    code: str
    name: str
    description: str


class AgentManagementGateOut(BaseModel):
    code: str
    name: str
    description: str
    forced: bool


class AgentManagementOut(BaseModel):
    code: AgentCode
    name: str
    group: AgentGroup
    enabled: bool
    responsibility: str
    system_prompt: str
    automation_level: AutomationLevel
    tool_permissions: dict[str, ToolPermissionMode]
    quality_gates: list[str]
    available_tools: list[AgentManagementToolOut]
    available_quality_gates: list[AgentManagementGateOut]
    typical_tasks: list[str]
    standard_outputs: list[DeliverableType]
    updated_at: datetime | None = None


class UpdateAgentManagementRequest(BaseModel):
    enabled: bool
    responsibility: str = Field(min_length=1, max_length=500)
    system_prompt: str = Field(default="", max_length=8000)
    tool_permissions: dict[str, ToolPermissionMode]
    quality_gates: list[str] = Field(default_factory=list, max_length=12)


class InvokeAgentRequest(BaseModel):
    project_id: int
    account_id: int
    source_task_id: int | None = None
    prompt: str = Field(min_length=1, max_length=4000)


class AgentDeliverableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_code: str
    type: DeliverableType
    version: int
    status: DeliverableStatus
    payload: dict
    created_at: datetime


class AgentKnowledgeSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category: KnowledgeCategory
    title: str
    source_label: str
    version: int


class InvokeAgentOut(BaseModel):
    task: BrainTaskOut
    invocation: AgentInvocationOut
    deliverable: AgentDeliverableOut
    acceptance: DeliverableAcceptanceOut
    knowledge_sources: list[AgentKnowledgeSourceOut]
    message: str


class AgentHandoffOut(BaseModel):
    task_id: int
    project_id: int
    account_id: int
    prompt: str
