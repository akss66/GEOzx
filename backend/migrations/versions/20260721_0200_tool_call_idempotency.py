"""Add durable idempotency keys to agent tool calls.

Revision ID: 20260721_0200
Revises: 20260721_0100
Create Date: 2026-07-21 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0200"
down_revision: str | None = "20260721_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_tool_calls",
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
    )
    op.create_unique_constraint(
        "uq_agent_tool_call_idempotency",
        "agent_tool_calls",
        ["org_id", "task_id", "tool_code", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_agent_tool_call_idempotency",
        "agent_tool_calls",
        type_="unique",
    )
    op.drop_column("agent_tool_calls", "idempotency_key")
