"""Persist daily metrics from Douyin account data exports.

Revision ID: 20260731_0300
Revises: 20260731_0200
Create Date: 2026-07-31 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0300"
down_revision: str | None = "20260731_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("account_metric_snapshots") as batch_op:
        batch_op.add_column(
            sa.Column("profile_visit_count", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("unfollow_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("like_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("comment_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("share_count", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("cover_click_rate", sa.Float(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("account_metric_snapshots") as batch_op:
        batch_op.drop_column("cover_click_rate")
        batch_op.drop_column("share_count")
        batch_op.drop_column("comment_count")
        batch_op.drop_column("like_count")
        batch_op.drop_column("unfollow_count")
        batch_op.drop_column("profile_visit_count")
