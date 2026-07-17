"""运营大脑领域模型。

这些表承载新的“决策 Agent + 专家团”任务模型；旧六阶段 pipeline 继续保留为参考执行链路。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin, pg_enum
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    AutomationLevel,
    BrainTaskStatus,
    BrainTaskType,
    DeliverableAcceptanceStatus,
    DeliverableType,
    Platform,
    RerunScope,
)


class BrainTask(Base, TimestampMixin):
    """运营大脑统筹的一次目标任务。"""

    __tablename__ = "brain_tasks"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="SET NULL"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    type: Mapped[BrainTaskType] = mapped_column(
        pg_enum(BrainTaskType, "brain_task_type"),
        default=BrainTaskType.CONTENT_CREATION,
        nullable=False,
    )
    status: Mapped[BrainTaskStatus] = mapped_column(
        pg_enum(BrainTaskStatus, "brain_task_status"),
        default=BrainTaskStatus.PENDING_CONFIRMATION,
        index=True,
        nullable=False,
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_focus: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    risk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    runtime_mode: Mapped[str] = mapped_column(
        String(40), default="legacy", server_default="legacy", nullable=False
    )
    thread_id: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    context_closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    brief: Mapped["TaskBrief"] = relationship(
        back_populates="task", cascade="all, delete-orphan", uselist=False
    )
    plan: Mapped["OrchestrationPlan"] = relationship(
        back_populates="task", cascade="all, delete-orphan", uselist=False
    )
    invocations: Mapped[list["AgentInvocation"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    tool_calls: Mapped[list["AgentToolCall"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    acceptances: Mapped[list["DeliverableAcceptance"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskBrief(Base, TimestampMixin):
    """用户目标被运营大脑结构化后的任务 Brief。"""

    __tablename__ = "task_briefs"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    project_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    account_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_groups.id", ondelete="SET NULL"), nullable=True
    )
    account_group_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    platforms: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    account_ids: Mapped[list[int]] = mapped_column(JSONVariant, default=list, nullable=False)
    cycle: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    content_goal: Mapped[str] = mapped_column(Text, default="", nullable=False)
    risk_constraints: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    expected_outputs: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    confirmation_actions: Mapped[list[str]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )

    task: Mapped[BrainTask] = relationship(back_populates="brief")


class OrchestrationPlan(Base, TimestampMixin):
    """运营大脑生成的可确认调度计划。"""

    __tablename__ = "orchestration_plans"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[list[dict]] = mapped_column(JSONVariant, default=list, nullable=False)
    quality_gates: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0, nullable=False)
    requires_human_confirmation: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    task: Mapped[BrainTask] = relationship(back_populates="plan")


class AgentInvocation(Base, TimestampMixin):
    """一次子 Agent 调用 Trace。"""

    __tablename__ = "agent_invocations"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    agent_code: Mapped[AgentCode] = mapped_column(
        pg_enum(AgentCode, "agent_code"), index=True, nullable=False
    )
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[AgentInvocationStatus] = mapped_column(
        pg_enum(AgentInvocationStatus, "agent_invocation_status"),
        default=AgentInvocationStatus.QUEUED,
        index=True,
        nullable=False,
    )
    input_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    output_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="deepseek-chat", nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    upstream: Mapped[list[int]] = mapped_column(JSONVariant, default=list, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped[BrainTask] = relationship(back_populates="invocations")
    tool_calls: Mapped[list["AgentToolCall"]] = relationship(
        back_populates="invocation", cascade="all, delete-orphan"
    )


class AgentToolCall(Base, TimestampMixin):
    """Durable tool-call ledger shared by agent modules."""

    __tablename__ = "agent_tool_calls"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    invocation_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_invocations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    module: Mapped[str] = mapped_column(String(64), default="brain", index=True, nullable=False)
    agent_code: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    tool_code: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="planned", index=True, nullable=False)
    permission_mode: Mapped[str] = mapped_column(String(40), default="auto", nullable=False)
    requires_human_confirmation: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    input_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    output_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0, nullable=False)
    meta: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped[BrainTask] = relationship(back_populates="tool_calls")
    invocation: Mapped[AgentInvocation | None] = relationship(back_populates="tool_calls")


class DeliverableAcceptance(Base, TimestampMixin):
    """按子 Agent 交付物进行的分项验收记录。"""

    __tablename__ = "deliverable_acceptances"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    deliverable_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL"), nullable=True
    )
    agent_code: Mapped[AgentCode] = mapped_column(pg_enum(AgentCode, "agent_code"), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False)
    deliverable_type: Mapped[DeliverableType] = mapped_column(
        pg_enum(DeliverableType, "deliverable_type"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    acceptance_items: Mapped[list[dict]] = mapped_column(JSONVariant, default=list, nullable=False)
    history_versions: Mapped[list[dict]] = mapped_column(JSONVariant, default=list, nullable=False)
    status: Mapped[DeliverableAcceptanceStatus] = mapped_column(
        pg_enum(DeliverableAcceptanceStatus, "deliverable_acceptance_status"),
        default=DeliverableAcceptanceStatus.PENDING,
        index=True,
        nullable=False,
    )
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    rerun_scope: Mapped[RerunScope | None] = mapped_column(
        pg_enum(RerunScope, "rerun_scope"), nullable=True
    )
    brain_rejudge_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    brain_rejudge_basis: Mapped[list[str]] = mapped_column(
        JSONVariant, default=list, nullable=False
    )

    task: Mapped[BrainTask] = relationship(back_populates="acceptances")


class AutomationPolicy(Base, TimestampMixin):
    """运营大脑自动化级别配置。"""

    __tablename__ = "automation_policies"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    account_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_groups.id", ondelete="CASCADE"), nullable=True
    )
    platform: Mapped[Platform | None] = mapped_column(
        pg_enum(Platform, "platform"), nullable=True
    )
    action_type: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[AutomationLevel] = mapped_column(
        pg_enum(AutomationLevel, "automation_level"),
        default=AutomationLevel.CONFIRM,
        nullable=False,
    )
