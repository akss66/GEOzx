"""Add a durable runtime-event idempotency key.

Revision ID: 20260728_0175
Revises: 20260728_0150
Create Date: 2026-07-28 17:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260728_0175"
down_revision: str | None = "20260728_0150"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("events") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint(
            "uq_events_idempotency_key", ["idempotency_key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("events") as batch_op:
        batch_op.drop_constraint("uq_events_idempotency_key", type_="unique")
        batch_op.drop_column("idempotency_key")
