"""Durable, versioned executions of business-facing agent skills."""

import hashlib
import json
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
    event,
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
            "'running', 'retry_wait', 'waiting_permission', 'needs_review', 'completed', "
            "'blocked', 'failed', 'cancelled', 'stopped'"
            ")",
            name="ck_skill_runs_status",
        ),
        UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_skill_runs_run_idempotency",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "run_id",
            "thread_id",
            "turn_id",
            name="uq_skill_runs_id_task_run_thread_turn",
        ),
        UniqueConstraint(
            "id",
            "task_id",
            "thread_id",
            "turn_id",
            name="uq_skill_runs_id_task_thread_turn",
        ),
        UniqueConstraint(
            "id",
            "run_id",
            "thread_id",
            "turn_id",
            name="uq_skill_runs_id_run_thread_turn",
        ),
        ForeignKeyConstraint(
            ["task_id", "org_id"],
            ["brain_tasks.id", "brain_tasks.org_id"],
            name="fk_skill_runs_task_org",
        ),
        ForeignKeyConstraint(
            ["thread_id", "org_id"],
            ["conversation_threads.id", "conversation_threads.org_id"],
            name="fk_skill_runs_thread_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["turn_id", "thread_id", "org_id"],
            [
                "conversation_turns.id",
                "conversation_turns.thread_id",
                "conversation_turns.org_id",
            ],
            name="fk_skill_runs_turn_thread_org",
            ondelete="CASCADE",
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
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id", "thread_id", "turn_id", "org_id"],
            [
                "agent_runs.id",
                "agent_runs.task_id",
                "agent_runs.thread_id",
                "agent_runs.turn_id",
                "agent_runs.org_id",
            ],
            name="fk_skill_runs_run_task_thread_turn_org",
            ondelete="CASCADE",
        ),
        Index("ix_skill_runs_org_status", "org_id", "status"),
        Index("ix_skill_runs_thread_created", "thread_id", "created_at"),
        Index("ix_skill_runs_turn_skill", "turn_id", "skill_code"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
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
    input_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
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


@event.listens_for(SkillRun, "before_insert")
def _freeze_skill_input_hash(_mapper, _connection, target: SkillRun) -> None:
    if target.input_hash:
        return
    normalized = json.dumps(
        dict(target.input_snapshot or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    target.input_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
