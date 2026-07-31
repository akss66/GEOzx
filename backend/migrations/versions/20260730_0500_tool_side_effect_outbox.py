"""Add durable tool side-effect classification and execution attempts.

Revision ID: 20260730_0500
Revises: 20260730_0400
Create Date: 2026-07-30 05:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0500"
down_revision: str | None = "20260730_0400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SIDE_EFFECT_CHECK = "side_effect_level IN ('read', 'idempotent_write', 'non_idempotent_write')"
_ATTEMPT_STATUS_CHECK = "status IN ('planned', 'dispatched', 'success', 'failed', 'ambiguous')"


def upgrade() -> None:
    with op.batch_alter_table("agent_tool_calls") as batch_op:
        batch_op.add_column(
            sa.Column(
                "provider_idempotency_key",
                sa.String(length=160),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "side_effect_level",
                sa.String(length=32),
                nullable=False,
                server_default="read",
            )
        )
        batch_op.create_check_constraint(
            "ck_agent_tool_calls_side_effect_level",
            _SIDE_EFFECT_CHECK,
        )

    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "tool_execution_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "tool_call_id",
            sa.BigInteger(),
            sa.ForeignKey("agent_tool_calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="planned",
        ),
        sa.Column(
            "provider_idempotency_key",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("meta", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            _ATTEMPT_STATUS_CHECK,
            name="ck_tool_execution_attempts_status",
        ),
        sa.UniqueConstraint(
            "tool_call_id",
            "attempt_no",
            name="uq_tool_execution_attempt_call_number",
        ),
    )
    op.create_index(
        "ix_tool_execution_attempts_tool_call_id",
        "tool_execution_attempts",
        ["tool_call_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_tool_execution_attempts_tool_call_id",
        table_name="tool_execution_attempts",
    )
    op.drop_table("tool_execution_attempts")
    with op.batch_alter_table("agent_tool_calls") as batch_op:
        batch_op.drop_constraint(
            "ck_agent_tool_calls_side_effect_level",
            type_="check",
        )
        batch_op.drop_column("side_effect_level")
        batch_op.drop_column("provider_idempotency_key")
