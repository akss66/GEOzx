"""Durable execution records for the operations-agent runtime."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin

if TYPE_CHECKING:
    from app.models.conversation import ConversationThread, ConversationTurn


class AgentRun(Base, TimestampMixin):
    """One idempotent user-triggered execution of the main agent."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "requested_by_id",
            "client_message_id",
            name="uq_agent_run_request",
        ),
        UniqueConstraint(
            "id",
            "thread_id",
            "turn_id",
            "org_id",
            name="uq_agent_runs_id_thread_turn_org",
        ),
        UniqueConstraint(
            "id",
            "thread_id",
            "turn_id",
            name="uq_agent_runs_id_thread_turn",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "thread_id",
            "turn_id",
            name="uq_agent_runs_id_task_thread_turn",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "thread_id",
            "turn_id",
            "org_id",
            name="uq_agent_runs_id_task_thread_turn_org",
        ),
        ForeignKeyConstraint(
            ["task_id", "org_id"],
            ["brain_tasks.id", "brain_tasks.org_id"],
            name="fk_agent_runs_task_org",
        ),
        ForeignKeyConstraint(
            ["thread_id", "org_id"],
            ["conversation_threads.id", "conversation_threads.org_id"],
            name="fk_agent_runs_thread_org",
        ),
        ForeignKeyConstraint(
            ["turn_id", "thread_id", "org_id"],
            [
                "conversation_turns.id",
                "conversation_turns.thread_id",
                "conversation_turns.org_id",
            ],
            name="fk_agent_runs_turn_thread_org",
        ),
        CheckConstraint(
            "turn_id IS NULL OR thread_id IS NOT NULL",
            name="ck_agent_runs_turn_requires_thread",
        ),
        CheckConstraint(
            "status IN ("
            "'claimed', 'waiting_predecessor', 'queued', 'running', "
            "'retry_wait', 'waiting_permission', 'waiting_decision', "
            "'waiting_user', 'completed', 'blocked', 'failed', "
            "'dead_letter', 'cancelled', 'stopped'"
            ")",
            name="ck_agent_runs_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    requested_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="CASCADE"), index=True, nullable=True
    )
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    turn_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_turns.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    client_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default="claimed", server_default="claimed", index=True, nullable=False
    )
    phase: Mapped[str] = mapped_column(
        String(80), default="request", server_default="request", nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=3, server_default="3", nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    result_payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)

    thread: Mapped["ConversationThread | None"] = relationship(
        back_populates="agent_runs",
        foreign_keys=[thread_id],
    )
    turn: Mapped["ConversationTurn | None"] = relationship(
        back_populates="agent_runs",
        foreign_keys=[turn_id],
    )
