"""Snapshot exact knowledge evidence on agent citations.

Revision ID: 20260811_0250
Revises: 20260811_0200
Create Date: 2026-08-11 02:50:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0250"
down_revision: str | None = "20260811_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_citations", sa.Column("entry_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "knowledge_citations", sa.Column("source_type", sa.String(length=40), nullable=True)
    )
    op.add_column(
        "knowledge_citations", sa.Column("source_label", sa.String(length=300), nullable=True)
    )
    op.add_column(
        "knowledge_citations", sa.Column("source_url", sa.String(length=1000), nullable=True)
    )
    op.add_column(
        "knowledge_citations", sa.Column("verification_status", sa.String(length=40), nullable=True)
    )
    op.add_column(
        "knowledge_citations", sa.Column("allowed_for_external_claim", sa.Boolean(), nullable=True)
    )
    op.create_index(
        "ix_knowledge_citations_entry_id_version",
        "knowledge_citations",
        ["entry_id", "entry_version"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_citations_entry_id_version", table_name="knowledge_citations")
    for column in (
        "allowed_for_external_claim",
        "verification_status",
        "source_url",
        "source_label",
        "source_type",
        "entry_version",
    ):
        op.drop_column("knowledge_citations", column)
