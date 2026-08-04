"""Persist recoverable human turn interrupts.

Revision ID: 20260804_0500
Revises: 20260804_0450
Create Date: 2026-08-05 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260804_0500"
down_revision: str | None = "20260804_0450"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "turn_interrupts",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("thread_id", BigIntPK, nullable=False),
        sa.Column("turn_id", BigIntPK, nullable=False),
        sa.Column("run_id", BigIntPK, nullable=False),
        sa.Column("skill_run_id", BigIntPK, nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("public_message", sa.Text, nullable=False),
        sa.Column("action_label", sa.String(240), nullable=True),
        sa.Column("response_schema", JSONVariant, nullable=False),
        sa.Column("source_type", sa.String(64), nullable=True),
        sa.Column("source_id", BigIntPK, nullable=True),
        sa.Column("source_version", sa.Integer, nullable=True),
        sa.Column("semantic_key", sa.String(160), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("resolution_payload", JSONVariant, nullable=True),
        sa.Column("resolution_hash", sa.String(64), nullable=True),
        sa.Column("resolution_idempotency_key", sa.String(160), nullable=True),
        sa.Column("resolved_by_id", BigIntPK, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('clarification', 'approval', 'manual_pause')",
            name="ck_turn_interrupts_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved', 'cancelled', 'expired', 'superseded')",
            name="ck_turn_interrupts_status",
        ),
        sa.CheckConstraint("version > 0", name="ck_turn_interrupts_version_positive"),
        sa.CheckConstraint(
            "(source_type IS NULL AND source_id IS NULL AND source_version IS NULL) OR "
            "(source_type IS NOT NULL AND source_id IS NOT NULL)",
            name="ck_turn_interrupts_source_identity",
        ),
        sa.CheckConstraint(
            "(status = 'resolved' AND resolution_payload IS NOT NULL "
            "AND resolution_hash IS NOT NULL AND resolution_idempotency_key IS NOT NULL "
            "AND resolved_by_id IS NOT NULL AND resolved_at IS NOT NULL) OR "
            "(status <> 'resolved' AND resolution_payload IS NULL "
            "AND resolution_hash IS NULL AND resolution_idempotency_key IS NULL "
            "AND resolved_by_id IS NULL AND resolved_at IS NULL)",
            name="ck_turn_interrupts_resolution_lifecycle",
        ),
        sa.UniqueConstraint(
            "run_id", "semantic_key", name="uq_turn_interrupts_run_semantic_key"
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "org_id"],
            ["accounts.id", "accounts.org_id"],
            name="fk_turn_interrupts_account_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id", "account_id", "org_id"],
            [
                "conversation_threads.id",
                "conversation_threads.account_id",
                "conversation_threads.org_id",
            ],
            name="fk_turn_interrupts_thread_account_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "thread_id", "org_id"],
            [
                "conversation_turns.id",
                "conversation_turns.thread_id",
                "conversation_turns.org_id",
            ],
            name="fk_turn_interrupts_turn_thread_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["resolved_by_id"],
            ["users.id"],
            name="fk_turn_interrupts_resolved_by",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "uq_turn_interrupts_effective_pending",
        "turn_interrupts",
        ["thread_id", "turn_id", "run_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_turn_interrupts_scope_status",
        "turn_interrupts",
        ["org_id", "account_id", "thread_id", "turn_id", "run_id", "status"],
    )
    op.create_index(
        "ix_turn_interrupts_source",
        "turn_interrupts",
        ["source_type", "source_id"],
    )
    op.create_index(
        "ix_turn_interrupts_resolved_by",
        "turn_interrupts",
        ["resolved_by_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_turn_interrupts_resolved_by", table_name="turn_interrupts")
    op.drop_index("ix_turn_interrupts_source", table_name="turn_interrupts")
    op.drop_index("ix_turn_interrupts_scope_status", table_name="turn_interrupts")
    op.drop_index("uq_turn_interrupts_effective_pending", table_name="turn_interrupts")
    op.drop_table("turn_interrupts")

