"""Auditable business ledgers for the AI COO operating loop."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin


class StrategyPlan(Base, TimestampMixin):
    """A versioned operating strategy grounded in persisted evidence."""

    __tablename__ = "strategy_plans"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "version",
            name="uq_strategy_plans_task_version",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), index=True, nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True, nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(40), default="draft", server_default="draft", index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    situation_snapshot: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    strategy: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    kpis: Mapped[list[dict]] = mapped_column(JSONVariant, default=list, nullable=False)
    risks: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    evidence_refs: Mapped[list[dict]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )
    rationale_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    prompt_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[str] = mapped_column(
        String(40), default="1.0", server_default="1.0", nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped["BrainTask"] = relationship(back_populates="strategy_plans")


class DecisionTrace(Base, TimestampMixin):
    """An auditable business decision summary, never a private chain of thought."""

    __tablename__ = "decision_traces"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "trace_key",
            name="uq_decision_traces_task_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), index=True, nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True, nullable=True
    )
    trace_key: Mapped[str] = mapped_column(String(160), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[dict]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )
    alternatives: Mapped[list[dict]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )
    selected_option: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    decision_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    action_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    outcome: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default="decided", server_default="decided", index=True, nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped["BrainTask"] = relationship(back_populates="decision_traces")


class ReflectionRecord(Base, TimestampMixin):
    """A comparison of intended and observed operating outcomes."""

    __tablename__ = "reflection_records"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "run_id",
            name="uq_reflection_records_task_run",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), index=True, nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True, nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(40),
        default="pending_observation",
        server_default="pending_observation",
        index=True,
        nullable=False,
    )
    goal_snapshot: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    expected_outcome: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    observed_outcome: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    evidence_refs: Mapped[list[dict]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )
    diagnosis: Mapped[list[dict]] = mapped_column(JSONVariant, default=list, nullable=False)
    conclusion: Mapped[str] = mapped_column(Text, default="", nullable=False)
    next_strategy: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    experience_candidates: Mapped[list[dict]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped["BrainTask"] = relationship(back_populates="reflection_records")
    experience_memories: Mapped[list["ExperienceMemory"]] = relationship(
        back_populates="reflection"
    )


class ExperienceMemory(Base, TimestampMixin):
    """A candidate or verified operating lesson backed by traceable sources."""

    __tablename__ = "experience_memories"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_experience_memories_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="SET NULL"), index=True, nullable=True
    )
    reflection_id: Mapped[int | None] = mapped_column(
        ForeignKey("reflection_records.id", ondelete="SET NULL"), index=True, nullable=True
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), index=True, nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True, nullable=True
    )
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(40), default="candidate", server_default="candidate", index=True, nullable=False
    )
    industry: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    condition: Mapped[str] = mapped_column(Text, default="", nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), default=0, server_default="0", nullable=False
    )
    source_refs: Mapped[list[dict]] = mapped_column(JSONVariant, default=list, nullable=False)
    verification_method: Mapped[str] = mapped_column(
        String(40), default="pending", server_default="pending", nullable=False
    )
    verification_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped["BrainTask | None"] = relationship(back_populates="experience_memories")
    reflection: Mapped[ReflectionRecord | None] = relationship(
        back_populates="experience_memories"
    )


class AgentQualityScore(Base, TimestampMixin):
    """A persisted Critic evaluation for an expert output."""

    __tablename__ = "agent_quality_scores"
    __table_args__ = (
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_agent_quality_scores_score",
        ),
        CheckConstraint(
            "iteration >= 0 AND iteration <= 2",
            name="ck_agent_quality_scores_iteration",
        ),
        UniqueConstraint(
            "task_id",
            "invocation_id",
            "iteration",
            name="uq_agent_quality_scores_invocation_iteration",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    invocation_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_invocations.id", ondelete="SET NULL"), index=True, nullable=True
    )
    deliverable_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL"), index=True, nullable=True
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    issues: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    suggestions: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    passed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", index=True, nullable=False
    )
    iteration: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    evidence_refs: Mapped[list[dict]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )
    critic_prompt_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    critic_prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    critic_prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    critic_model: Mapped[str | None] = mapped_column(String(160), nullable=True)

    task: Mapped["BrainTask"] = relationship(back_populates="quality_scores")


from app.models.brain import BrainTask  # noqa: E402
