"""Strict contracts shared by the AI COO runtime and API."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator

EvidenceSourceType = Literal[
    "account_metric_snapshot",
    "platform_content_record",
    "data_import_batch",
    "audience_profile_snapshot",
    "benchmark_snapshot",
    "metric_snapshot",
    "platform_publish_job",
    "knowledge_entry",
    "manual_confirmation",
    "external_tool",
]
EvidenceFreshness = Literal["fresh", "stale", "unknown"]


class EvidenceTimeRange(BaseModel):
    start: str | None = None
    end: str | None = None


class EvidenceRef(BaseModel):
    """A traceable persisted or externally verified fact used by a decision."""

    model_config = ConfigDict(extra="forbid")

    source_type: EvidenceSourceType
    source_id: str = Field(min_length=1, max_length=200)
    metric: str = Field(min_length=1, max_length=160)
    value: Any
    time_range: EvidenceTimeRange
    collected_at: datetime
    freshness: EvidenceFreshness


class CriticDimensions(BaseModel):
    """The five non-negotiable quality dimensions for specialist output."""

    model_config = ConfigDict(extra="forbid")

    brand_consistency: int = Field(ge=0, le=100)
    user_value: int = Field(ge=0, le=100)
    propagation_ability: int = Field(ge=0, le=100)
    commercial_conversion: int = Field(ge=0, le=100)
    factual_accuracy: int = Field(ge=0, le=100)


class CriticEvaluation(BaseModel):
    """Strict model output; pass/fail is calculated by server policy."""

    model_config = ConfigDict(extra="forbid")

    dimensions: CriticDimensions
    issues: list[str] = Field(default_factory=list, max_length=20)
    suggestions: list[str] = Field(default_factory=list, max_length=20)


class OperatingDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: str = Field(min_length=2, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class OperatingKPI(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1, max_length=160)
    baseline: FiniteFloat | None = None
    target: str | FiniteFloat
    direction: Literal["increase", "decrease", "maintain", "observe"] = "increase"
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str | float) -> str | float:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("text KPI target cannot be empty")
            if len(normalized) > 300:
                raise ValueError("text KPI target cannot exceed 300 characters")
            return normalized
        return value


class OperatingStrategyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_days: Literal[30] = 30
    primary_action: str = Field(min_length=2, max_length=500)
    content_mix: dict[str, int] = Field(default_factory=dict, max_length=20)
    stage_goals: list[str] = Field(min_length=1, max_length=12)
    content_direction: list[str] = Field(default_factory=list, max_length=20)
    user_strategy: list[str] = Field(default_factory=list, max_length=20)
    conversion_path: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_content_mix(self) -> Self:
        if any(value < 0 or value > 100 for value in self.content_mix.values()):
            raise ValueError("content mix values must be between 0 and 100")
        if self.content_mix and sum(self.content_mix.values()) != 100:
            raise ValueError("content mix values must total 100")
        return self


class OperatingStrategyDraft(BaseModel):
    """Strict output contract for the evidence-grounded Strategy Agent."""

    model_config = ConfigDict(extra="forbid")

    account_stage: Literal["unknown", "setup", "growth", "conversion", "retention"]
    main_problem: str = Field(default="", max_length=500)
    data_sufficiency: Literal["insufficient", "partial", "sufficient"]
    missing_data: list[str] = Field(default_factory=list, max_length=30)
    confidence: Decimal = Field(ge=0, le=1)
    diagnosis: list[OperatingDiagnosis] = Field(default_factory=list, max_length=20)
    strategy: OperatingStrategyBody
    kpis: list[OperatingKPI] = Field(default_factory=list, max_length=20)
    risks: list[str] = Field(default_factory=list, max_length=30)
    rationale_summary: str = Field(min_length=2, max_length=2000)
    required_expert_codes: list[
        Literal[
            "01-positioning",
            "02-content-director",
            "03-art-director",
            "04-video-creator",
            "05-editor",
            "06-operator",
            "07-advertiser",
            "08-customer-service",
        ]
    ] = Field(default_factory=list, max_length=8)


class BusinessMemoryLayer(BaseModel):
    """Stable organization, client, project and reviewed business knowledge."""

    model_config = ConfigDict(extra="forbid")

    org_id: int
    org_name: str
    clients: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    projects: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    reviewed_knowledge: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=12,
    )


class AccountMemoryLayer(BaseModel):
    """Current account identity and latest evidence-grounded situation."""

    model_config = ConfigDict(extra="forbid")

    account_id: int | None = None
    nickname: str = ""
    platform: str = ""
    external_account_id: str | None = None
    situation_summary: dict[str, Any] = Field(default_factory=dict)


class ContentMemoryLayer(BaseModel):
    """A bounded recent-content projection, not the full content warehouse."""

    model_config = ConfigDict(extra="forbid")

    recent_items: list[dict[str, Any]] = Field(default_factory=list, max_length=12)


class ExperienceMemoryItem(BaseModel):
    """A human- or data-verified operating lesson."""

    model_config = ConfigDict(extra="forbid")

    id: int
    industry: str
    action: str
    condition: str
    result: str
    confidence: Decimal = Field(ge=0, le=1)
    source_refs: list[dict[str, Any]] = Field(min_length=1, max_length=30)


class ExperienceMemoryLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExperienceMemoryItem] = Field(default_factory=list, max_length=12)


class COOMemoryContext(BaseModel):
    """Four semantic memory layers projected from durable, scoped ledgers."""

    model_config = ConfigDict(extra="forbid")

    business: BusinessMemoryLayer
    account: AccountMemoryLayer
    content: ContentMemoryLayer
    experience: ExperienceMemoryLayer


class COORuntimeState(BaseModel):
    """Serializable business state for the durable AI COO graph."""

    model_config = ConfigDict(extra="forbid")

    task_id: int
    run_id: int | None = None
    thread_id: str = Field(min_length=1, max_length=120)
    org_id: int
    available_client_ids: list[int] = Field(default_factory=list)
    available_project_ids: list[int] = Field(default_factory=list)
    active_client_id: int | None = None
    active_project_id: int | None = None
    account_id: int | None = None
    user_goal: str = Field(min_length=1)
    normalized_goal: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    situation_summary: dict[str, Any] = Field(default_factory=dict)
    memory_context: COOMemoryContext | None = None
    strategy_plan_id: int | None = None
    task_plan: list[dict[str, Any]] = Field(default_factory=list)
    agent_invocation_ids: list[int] = Field(default_factory=list)
    deliverable_ids: list[int] = Field(default_factory=list)
    quality_score_ids: list[int] = Field(default_factory=list)
    pending_approval_ids: list[int] = Field(default_factory=list)
    publish_job_ids: list[int] = Field(default_factory=list)
    performance_snapshot_ids: list[int] = Field(default_factory=list)
    reflection_record_id: int | None = None
    experience_candidate_ids: list[int] = Field(default_factory=list)
    next_strategy: dict[str, Any] = Field(default_factory=dict)
    phase: str = Field(min_length=1, max_length=80)
    iteration: int = Field(default=0, ge=0, le=2)
    retry_budget: int = Field(default=2, ge=0, le=2)
    token_count: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=0)
    status: str = Field(default="running", min_length=1, max_length=40)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class StrategyPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    run_id: int | None
    client_id: int | None
    project_id: int | None
    account_id: int | None
    status: str
    version: int
    goal: str
    situation_snapshot: dict[str, Any]
    strategy: dict[str, Any]
    kpis: list[dict[str, Any]]
    risks: list[str]
    evidence_refs: list[dict[str, Any]]
    rationale_summary: str
    prompt_id: str | None
    prompt_version: str | None
    prompt_hash: str | None
    schema_version: str
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DecisionTraceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    run_id: int | None
    client_id: int | None
    project_id: int | None
    account_id: int | None
    trace_key: str
    goal: str
    evidence_refs: list[dict[str, Any]]
    alternatives: list[dict[str, Any]]
    selected_option: dict[str, Any]
    decision_reason: str
    action_summary: str
    outcome: dict[str, Any]
    status: str
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentQualityScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    run_id: int | None
    invocation_id: int | None
    deliverable_id: int | None
    score: int
    dimensions: dict[str, Any]
    issues: list[str]
    suggestions: list[str]
    passed: bool
    iteration: int
    evidence_refs: list[dict[str, Any]]
    critic_prompt_id: str | None
    critic_prompt_version: str | None
    critic_prompt_hash: str | None
    critic_model: str | None
    created_at: datetime
    updated_at: datetime


class ReflectionRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    run_id: int | None
    client_id: int | None
    project_id: int | None
    account_id: int | None
    status: str
    goal_snapshot: dict[str, Any]
    expected_outcome: dict[str, Any]
    observed_outcome: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    diagnosis: list[dict[str, Any]]
    conclusion: str
    next_strategy: dict[str, Any]
    experience_candidates: list[dict[str, Any]]
    measured_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExperienceMemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int | None
    reflection_id: int | None
    client_id: int | None
    project_id: int | None
    account_id: int | None
    verified_by_id: int | None
    status: str
    industry: str
    action: str
    condition: str
    result: str
    confidence: Decimal
    source_refs: list[dict[str, Any]]
    verification_method: str
    verification_note: str
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExperienceVerificationRequest(BaseModel):
    candidate_key: str = Field(min_length=1, max_length=120)
    verification_note: str = Field(min_length=2, max_length=1000)


class OperationIntelligenceOut(BaseModel):
    task_id: int
    score: int = Field(ge=0, le=100)
    components: dict[str, int]
    weights: dict[str, float]
    basis: list[str] = Field(default_factory=list)
    data_sufficiency: Literal["insufficient", "partial", "sufficient"]
    calculated_at: datetime


class AccountSituationOut(BaseModel):
    account_id: int
    generated_at: datetime
    data_sufficiency: Literal["insufficient", "partial", "sufficient"]
    account_stage: str | None = None
    main_problem: str | None = None
    conclusion: str
    diagnosis: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    confidence: Decimal = Field(default=Decimal("0"), ge=0, le=1)
