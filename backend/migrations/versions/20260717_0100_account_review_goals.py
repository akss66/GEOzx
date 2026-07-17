"""Add account-scoped rolling review goals.

Revision ID: 20260717_0100
Revises: 20260716_0200
Create Date: 2026-07-17 03:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260717_0100"
down_revision: str | None = "20260716_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_review_goals",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("target_play", sa.Integer(), nullable=True),
        sa.Column("target_completion_rate", sa.Float(), nullable=True),
        sa.Column("target_follower_delta", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "period_days",
            name="uq_account_review_goals_account_period",
        ),
    )
    op.create_index(op.f("ix_account_review_goals_org_id"), "account_review_goals", ["org_id"])
    op.create_index(
        op.f("ix_account_review_goals_account_id"),
        "account_review_goals",
        ["account_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_account_review_goals_account_id"), table_name="account_review_goals")
    op.drop_index(op.f("ix_account_review_goals_org_id"), table_name="account_review_goals")
    op.drop_table("account_review_goals")
