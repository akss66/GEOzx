"""Track authoritative source identity for platform content.

Revision ID: 20260727_0200
Revises: 20260727_0100
Create Date: 2026-07-27 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.base import JSONVariant

revision: str = "20260727_0200"
down_revision: str | None = "20260727_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

source_kind_enum = postgresql.ENUM(
    "official_api",
    "platform_export",
    "screenshot_verified",
    "manual_entry",
    name="data_source_kind",
    create_type=False,
)


def _source_kind_type() -> sa.types.TypeEngine:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return source_kind_enum
    return sa.String(length=40)


def upgrade() -> None:
    op.add_column(
        "platform_content_records",
        sa.Column(
            "source_kind",
            _source_kind_type(),
            server_default="platform_export",
            nullable=False,
        ),
    )
    op.add_column(
        "platform_content_records",
        sa.Column(
            "source_metadata",
            JSONVariant,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE platform_content_records
            SET source_kind = COALESCE(
                (
                    SELECT data_import_batches.source_kind
                    FROM data_import_batches
                    WHERE data_import_batches.id =
                        platform_content_records.canonical_import_batch_id
                ),
                'platform_export'
            )
            """
        )
    )
    op.create_index(
        "ix_platform_content_records_source_kind",
        "platform_content_records",
        ["source_kind"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_content_records_source_kind",
        table_name="platform_content_records",
    )
    op.drop_column("platform_content_records", "source_metadata")
    op.drop_column("platform_content_records", "source_kind")
