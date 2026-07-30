"""Durable, versioned executions of business-facing agent skills."""

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin

if TYPE_CHECKING:
    from app.models.agent_runtime import AgentRun
    from app.models.brain import AgentInvocation, AgentToolCall, BrainTask
    from app.models.conversation import ConversationThread, ConversationTurn


class SkillRun(Base, TimestampMixin):
    """One idempotent execution of a versioned Skill for a conversation turn."""

    __tablename__ = "skill_runs"
    __table_args__ = (
        CheckConstraint(
            "skill_version > 0",
            name="ck_skill_runs_skill_version_positive",
        ),
        CheckConstraint(
            "status IN ("
            "'running', 'retry_wait', 'waiting_permission', 'completed', "
            "'blocked', 'failed', 'cancelled', 'stopped'"
            ")",
            name="ck_skill_runs_status",
        ),
        UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_skill_runs_run_idempotency",
        ),
        ForeignKeyConstraint(
            ["thread_id", "org_id"],
            ["conversation_threads.id", "conversation_threads.org_id"],
            name="fk_skill_runs_thread_org",
        ),
        ForeignKeyConstraint(
            ["turn_id", "thread_id", "org_id"],
            [
                "conversation_turns.id",
                "conversation_turns.thread_id",
                "conversation_turns.org_id",
            ],
            name="fk_skill_runs_turn_thread_org",
        ),
        ForeignKeyConstraint(
            ["run_id", "thread_id", "turn_id", "org_id"],
            [
                "agent_runs.id",
                "agent_runs.thread_id",
                "agent_runs.turn_id",
                "agent_runs.org_id",
            ],
            name="fk_skill_runs_run_thread_turn_org",
        ),
        Index("ix_skill_runs_org_status", "org_id", "status"),
        Index("ix_skill_runs_thread_created", "thread_id", "created_at"),
        Index("ix_skill_runs_turn_skill", "turn_id", "skill_code"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[int] = mapped_column(
        ForeignKey("conversation_turns.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="SET NULL"), index=True, nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    skill_code: Mapped[str] = mapped_column(String(120), nullable=False)
    skill_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    output_snapshot: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)

    thread: Mapped["ConversationThread"] = relationship(foreign_keys=[thread_id])
    turn: Mapped["ConversationTurn"] = relationship(foreign_keys=[turn_id])
    run: Mapped["AgentRun"] = relationship(foreign_keys=[run_id])
    task: Mapped["BrainTask | None"] = relationship(
        back_populates="skill_runs",
        foreign_keys=[task_id],
    )
    invocations: Mapped[list["AgentInvocation"]] = relationship(
        back_populates="skill_run",
        foreign_keys="AgentInvocation.skill_run_id",
    )
    tool_calls: Mapped[list["AgentToolCall"]] = relationship(
        back_populates="skill_run",
        foreign_keys="AgentToolCall.skill_run_id",
    )

    @validates("skill_version")
    def validate_skill_version(self, _key: str, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("skill_version must be a positive integer")
        return value
