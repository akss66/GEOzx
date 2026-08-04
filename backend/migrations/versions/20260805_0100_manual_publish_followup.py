"""Persist the actual completion time for manual publication.

Revision ID: 20260805_0100
Revises: 20260804_0500
Create Date: 2026-08-05 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0100"
down_revision: str | None = "20260804_0500"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "content_schedule_entries",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_content_schedule_entries_publication_followup",
        "content_schedule_entries",
        ["org_id", "account_id", "created_by_id", "status", "published_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_content_schedule_entries_publication_followup",
        table_name="content_schedule_entries",
    )
    op.drop_column("content_schedule_entries", "published_at")
