"""Add monthly project cost budgets.

Revision ID: 20260717_0300
Revises: 20260717_0200
Create Date: 2026-07-17 05:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_0300"
down_revision: str | None = "20260717_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("monthly_cost_budget_usd", sa.Numeric(12, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "monthly_cost_budget_usd")
