"""Add nullable per-Turn latency and provider-attempt telemetry.

Revision ID: 20260730_0600
Revises: 20260730_0500
Create Date: 2026-07-30 06:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0600"
down_revision: str | None = "20260730_0500"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_METRICS = (
    "route_ms",
    "first_token_ms",
    "completion_ms",
    "total_ms",
    "model_call_count",
)


def upgrade() -> None:
    # Deliberately no server defaults or UPDATE backfill: historical Turn
    # telemetry is unknown and must remain NULL.
    with op.batch_alter_table("conversation_turns") as batch_op:
        for name in _METRICS:
            batch_op.add_column(sa.Column(name, sa.Integer(), nullable=True))
        for name in _METRICS:
            batch_op.create_check_constraint(
                f"ck_conversation_turns_{name}",
                f"{name} IS NULL OR {name} >= 0",
            )


def downgrade() -> None:
    with op.batch_alter_table("conversation_turns") as batch_op:
        for name in reversed(_METRICS):
            batch_op.drop_constraint(
                f"ck_conversation_turns_{name}",
                type_="check",
            )
        for name in reversed(_METRICS):
            batch_op.drop_column(name)
