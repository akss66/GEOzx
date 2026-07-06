"""task brief account scope

Revision ID: 9d2f6a7b8c10
Revises: 84b1d2c3e4f5
Create Date: 2026-07-01 02:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "9d2f6a7b8c10"
down_revision: str | None = "84b1d2c3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_briefs",
        sa.Column("account_ids", sa.JSON(), server_default="[]", nullable=False),
    )
    op.alter_column("task_briefs", "account_ids", server_default=None)


def downgrade() -> None:
    op.drop_column("task_briefs", "account_ids")
