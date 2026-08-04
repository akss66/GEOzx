"""Add durable conversation turn steering lineage.

Revision ID: 20260804_0300
Revises: 20260804_0200
Create Date: 2026-08-04 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260804_0300"
down_revision: str | None = "20260804_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversation_turns") as batch_op:
        batch_op.add_column(sa.Column("target_turn_id", BigIntPK, nullable=True))
        batch_op.add_column(
            sa.Column("steering_mode", sa.String(length=32), nullable=True)
        )
        batch_op.create_index(
            "ix_conversation_turns_target_turn_id",
            ["target_turn_id"],
        )
        batch_op.create_foreign_key(
            "fk_conversation_turn_target_turn_thread_org",
            "conversation_turns",
            ["target_turn_id", "thread_id", "org_id"],
            ["id", "thread_id", "org_id"],
        )
        batch_op.create_check_constraint(
            "ck_conversation_turns_steering_lineage",
            "(steering_mode IS NULL AND target_turn_id IS NULL) OR "
            "(steering_mode = 'independent_query' AND target_turn_id IS NULL) OR "
            "(steering_mode IN ('supplement', 'stop', 'replace_goal') "
            "AND target_turn_id IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            "ck_conversation_turn_target_not_self",
            "target_turn_id IS NULL OR target_turn_id != id",
        )


def downgrade() -> None:
    with op.batch_alter_table("conversation_turns") as batch_op:
        batch_op.drop_constraint(
            "ck_conversation_turn_target_not_self",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_conversation_turns_steering_lineage",
            type_="check",
        )
        batch_op.drop_constraint(
            "fk_conversation_turn_target_turn_thread_org",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_conversation_turns_target_turn_id")
        batch_op.drop_column("steering_mode")
        batch_op.drop_column("target_turn_id")
