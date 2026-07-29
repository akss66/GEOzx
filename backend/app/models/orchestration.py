"""编排域：AgentTask（任务状态机）、Event（事件溯源）、GateApproval（质量门审批）。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin, pg_enum
from app.models.enums import AgentTaskStatus, ContentStage, GateStatus, GateType

if TYPE_CHECKING:
    from app.models.content import ContentItem


class AgentTask(Base, TimestampMixin):
    """Agent 任务状态机：pending / running / done / failed / blocked。"""

    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    agent_code: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[ContentStage] = mapped_column(pg_enum(ContentStage, "task_stage"), nullable=False)
    status: Mapped[AgentTaskStatus] = mapped_column(
        pg_enum(AgentTaskStatus, "agent_task_status"),
        default=AgentTaskStatus.PENDING,
        index=True,
        nullable=False,
    )
    output_deliverable_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL"), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    content_item: Mapped[ContentItem] = relationship(back_populates="tasks")  # noqa: F821


class Event(Base):
    """事件日志 / 事件溯源。created_at 由 server_default 提供（事件不可变，无 updated_at）。"""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_events_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    type: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    content_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    turn_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_turns.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    skill_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("skill_runs.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    payload: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GateApproval(Base, TimestampMixin):
    """质量门审批记录：门类型、决策、审批人、意见。"""

    __tablename__ = "gate_approvals"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    gate: Mapped[GateType] = mapped_column(pg_enum(GateType, "gate_type"), nullable=False)
    status: Mapped[GateStatus] = mapped_column(
        pg_enum(GateStatus, "gate_status"),
        default=GateStatus.PENDING,
        index=True,
        nullable=False,
    )
    decided_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
