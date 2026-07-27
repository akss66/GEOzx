"""Promote imported content insight fields into business projections.

Revision ID: 20260723_0100
Revises: 20260722_0200
Create Date: 2026-07-23 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0100"
down_revision: str | None = "20260722_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("platform_content_records") as batch_op:
        batch_op.add_column(sa.Column("content_format", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("review_status", sa.String(length=80), nullable=True))

    with op.batch_alter_table("metric_snapshots") as batch_op:
        batch_op.add_column(sa.Column("completion_rate_5s", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("bounce_rate_2s", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("profile_visit_count", sa.Integer(), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE platform_content_records AS content
                SET content_format = NULLIF(rows.normalized_values ->> 'content_format', ''),
                    review_status = NULLIF(rows.normalized_values ->> 'review_status', '')
                FROM data_import_rows AS rows
                WHERE content.org_id = rows.org_id
                  AND content.account_id = rows.account_id
                  AND content.canonical_import_batch_id = rows.batch_id
                  AND content.canonical_import_row_number = rows.row_number
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE metric_snapshots AS metrics
                SET completion_rate_5s =
                        CAST(
                            NULLIF(rows.normalized_values ->> 'completion_rate_5s', '')
                            AS double precision
                        ),
                    bounce_rate_2s =
                        CAST(
                            NULLIF(rows.normalized_values ->> 'bounce_rate_2s', '')
                            AS double precision
                        ),
                    profile_visit_count =
                        NULLIF(rows.normalized_values ->> 'profile_visit_count', '')::integer
                FROM data_import_rows AS rows
                WHERE metrics.org_id = rows.org_id
                  AND metrics.account_id = rows.account_id
                  AND metrics.import_batch_id = rows.batch_id
                  AND metrics.platform_content_record_id = rows.platform_content_record_id
                """
            )
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            sa.text(
                """
                UPDATE platform_content_records
                SET content_format = (
                        SELECT NULLIF(json_extract(rows.normalized_values, '$.content_format'), '')
                        FROM data_import_rows AS rows
                        WHERE platform_content_records.org_id = rows.org_id
                          AND platform_content_records.account_id = rows.account_id
                          AND platform_content_records.canonical_import_batch_id = rows.batch_id
                          AND platform_content_records.canonical_import_row_number = rows.row_number
                    ),
                    review_status = (
                        SELECT NULLIF(json_extract(rows.normalized_values, '$.review_status'), '')
                        FROM data_import_rows AS rows
                        WHERE platform_content_records.org_id = rows.org_id
                          AND platform_content_records.account_id = rows.account_id
                          AND platform_content_records.canonical_import_batch_id = rows.batch_id
                          AND platform_content_records.canonical_import_row_number = rows.row_number
                    )
                WHERE canonical_import_batch_id IS NOT NULL
                """
            )
        )
        op.execute(
            sa.text(
                """
                UPDATE metric_snapshots
                SET completion_rate_5s = (
                        SELECT json_extract(rows.normalized_values, '$.completion_rate_5s')
                        FROM data_import_rows AS rows
                        WHERE metric_snapshots.org_id = rows.org_id
                          AND metric_snapshots.account_id = rows.account_id
                          AND metric_snapshots.import_batch_id = rows.batch_id
                          AND metric_snapshots.platform_content_record_id =
                              rows.platform_content_record_id
                    ),
                    bounce_rate_2s = (
                        SELECT json_extract(rows.normalized_values, '$.bounce_rate_2s')
                        FROM data_import_rows AS rows
                        WHERE metric_snapshots.org_id = rows.org_id
                          AND metric_snapshots.account_id = rows.account_id
                          AND metric_snapshots.import_batch_id = rows.batch_id
                          AND metric_snapshots.platform_content_record_id =
                              rows.platform_content_record_id
                    ),
                    profile_visit_count = (
                        SELECT json_extract(rows.normalized_values, '$.profile_visit_count')
                        FROM data_import_rows AS rows
                        WHERE metric_snapshots.org_id = rows.org_id
                          AND metric_snapshots.account_id = rows.account_id
                          AND metric_snapshots.import_batch_id = rows.batch_id
                          AND metric_snapshots.platform_content_record_id =
                              rows.platform_content_record_id
                    )
                WHERE import_batch_id IS NOT NULL
                """
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("metric_snapshots") as batch_op:
        batch_op.drop_column("profile_visit_count")
        batch_op.drop_column("bounce_rate_2s")
        batch_op.drop_column("completion_rate_5s")

    with op.batch_alter_table("platform_content_records") as batch_op:
        batch_op.drop_column("review_status")
        batch_op.drop_column("content_format")
