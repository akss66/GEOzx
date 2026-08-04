"""Durable human-in-the-loop state for one conversation execution."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin

NullableJSONVariant = JSON(none_as_null=True).with_variant(
    JSONB(none_as_null=True),
    "postgresql",
)


class TurnInterrupt(Base, TimestampMixin):
    """The sole durable truth for a recoverable human pause."""

    __tablename__ = "turn_interrupts"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('clarification', 'approval', 'manual_pause')",
            name="ck_turn_interrupts_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'resolved', 'cancelled', 'expired', 'superseded')",
            name="ck_turn_interrupts_status",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_turn_interrupts_version_positive",
        ),
        CheckConstraint(
            "(source_type IS NULL AND source_id IS NULL AND source_version IS NULL) OR "
            "(source_type IS NOT NULL AND source_id IS NOT NULL)",
            name="ck_turn_interrupts_source_identity",
        ),
        CheckConstraint(
            "(status = 'resolved' AND resolution_payload IS NOT NULL "
            "AND resolution_hash IS NOT NULL AND resolution_idempotency_key IS NOT NULL "
            "AND resolved_by_id IS NOT NULL AND resolved_at IS NOT NULL) OR "
            "(status <> 'resolved' AND resolution_payload IS NULL "
            "AND resolution_hash IS NULL AND resolution_idempotency_key IS NULL "
            "AND resolved_by_id IS NULL AND resolved_at IS NULL)",
            name="ck_turn_interrupts_resolution_lifecycle",
        ),
        UniqueConstraint(
            "run_id",
            "semantic_key",
            name="uq_turn_interrupts_run_semantic_key",
        ),
        ForeignKeyConstraint(
            ["account_id", "org_id"],
            ["accounts.id", "accounts.org_id"],
            name="fk_turn_interrupts_account_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["thread_id", "account_id", "org_id"],
            [
                "conversation_threads.id",
                "conversation_threads.account_id",
                "conversation_threads.org_id",
            ],
            name="fk_turn_interrupts_thread_account_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["turn_id", "thread_id", "org_id"],
            [
                "conversation_turns.id",
                "conversation_turns.thread_id",
                "conversation_turns.org_id",
            ],
            name="fk_turn_interrupts_turn_thread_org",
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
            name="fk_turn_interrupts_run_thread_turn_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["skill_run_id", "run_id", "thread_id", "turn_id"],
            [
                "skill_runs.id",
                "skill_runs.run_id",
                "skill_runs.thread_id",
                "skill_runs.turn_id",
            ],
            name="fk_turn_interrupts_skill_run_scope",
            ondelete="CASCADE",
        ),
        Index(
            "uq_turn_interrupts_effective_pending",
            "thread_id",
            "turn_id",
            "run_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index(
            "ix_turn_interrupts_scope_status",
            "org_id",
            "account_id",
            "thread_id",
            "turn_id",
            "run_id",
            "status",
        ),
        Index(
            "ix_turn_interrupts_source",
            "source_type",
            "source_id",
        ),
        Index(
            "ix_turn_interrupts_resolved_by",
            "resolved_by_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    account_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    thread_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    turn_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    run_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    skill_run_id: Mapped[int | None] = mapped_column(BigIntPK, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", nullable=False
    )
    public_message: Mapped[str] = mapped_column(Text, nullable=False)
    action_label: Mapped[str | None] = mapped_column(String(240), nullable=True)
    response_schema: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_id: Mapped[int | None] = mapped_column(BigIntPK, nullable=True)
    source_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    semantic_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    resolution_payload: Mapped[dict | None] = mapped_column(
        NullableJSONVariant, nullable=True
    )
    resolution_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution_idempotency_key: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    resolved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

