"""Strict contracts shared by the AI COO runtime and API."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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
