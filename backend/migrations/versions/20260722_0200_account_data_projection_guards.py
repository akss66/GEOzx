"""Add durable projection identity guards for account-data imports.

Revision ID: 20260722_0200
Revises: 20260722_0100
Create Date: 2026-07-22 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260722_0200"
down_revision: str | None = "20260722_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("platform_content_records") as batch_op:
        batch_op.add_column(sa.Column("canonical_import_row_number", sa.Integer(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_platform_content_records_import_row_identity",
            ["account_id", "canonical_import_batch_id", "canonical_import_row_number"],
        )

    with op.batch_alter_table("metric_snapshots") as batch_op:
        batch_op.create_unique_constraint(
            "uq_metric_snapshots_import_projection",
            ["account_id", "import_batch_id", "platform_content_record_id", "stat_date"],
        )


def downgrade() -> None:
    with op.batch_alter_table("metric_snapshots") as batch_op:
        batch_op.drop_constraint("uq_metric_snapshots_import_projection", type_="unique")

    with op.batch_alter_table("platform_content_records") as batch_op:
        batch_op.drop_constraint("uq_platform_content_records_import_row_identity", type_="unique")
        batch_op.drop_column("canonical_import_row_number")
