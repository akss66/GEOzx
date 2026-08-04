"""Make source-linked manual schedule slots idempotent.

Revision ID: 20260805_0200
Revises: 20260805_0100
Create Date: 2026-08-05 15:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260805_0200"
down_revision: str | None = "20260805_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "uq_content_schedule_source_slot"
_COLUMNS = [
    "org_id",
    "account_id",
    "source_artifact_id",
    "source_artifact_version",
    "scheduled_at",
]


def upgrade() -> None:
    with op.batch_alter_table("content_schedule_entries") as batch:
        batch.create_unique_constraint(_NAME, _COLUMNS)


def downgrade() -> None:
    with op.batch_alter_table("content_schedule_entries") as batch:
        batch.drop_constraint(_NAME, type_="unique")
