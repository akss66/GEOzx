"""Add per-Turn tool-attempt telemetry.

Revision ID: 20260803_0400
Revises: 20260803_0300
Create Date: 2026-08-03 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0400"
down_revision: str | None = "20260803_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversation_turns") as batch_op:
        batch_op.add_column(sa.Column("tool_call_count", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_conversation_turns_tool_call_count",
            "tool_call_count IS NULL OR tool_call_count >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("conversation_turns") as batch_op:
        batch_op.drop_constraint(
            "ck_conversation_turns_tool_call_count",
            type_="check",
        )
        batch_op.drop_column("tool_call_count")
