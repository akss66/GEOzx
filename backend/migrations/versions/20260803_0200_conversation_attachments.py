"""Add owned, account-scoped conversation attachments.

Revision ID: 20260803_0200
Revises: 20260803_0100
Create Date: 2026-08-03 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import JSONVariant

revision: str = "20260803_0200"
down_revision: str | None = "20260803_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_attachments",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("scan_status", sa.String(length=32), nullable=False),
        sa.Column("parse_status", sa.String(length=32), nullable=False),
        sa.Column("parsed_context", JSONVariant, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_conversation_attachments_size_positive"),
        sa.CheckConstraint(
            "scan_status IN ('pending', 'clean', 'rejected')",
            name="ck_conversation_attachments_scan_status",
        ),
        sa.CheckConstraint(
            "parse_status IN ('pending', 'ready', 'failed')",
            name="ck_conversation_attachments_parse_status",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["conversation_threads.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("storage_key", name="uq_conversation_attachments_storage_key"),
    )
    op.create_index(
        "ix_conversation_attachments_owner_scope",
        "conversation_attachments",
        ["org_id", "created_by_id", "account_id", "thread_id"],
    )
    op.create_index(
        "ix_conversation_attachments_sha256",
        "conversation_attachments",
        ["sha256"],
    )
    op.create_index(
        "ix_conversation_attachments_thread_created",
        "conversation_attachments",
        ["thread_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_attachments_thread_created",
        table_name="conversation_attachments",
    )
    op.drop_index("ix_conversation_attachments_sha256", table_name="conversation_attachments")
    op.drop_index(
        "ix_conversation_attachments_owner_scope",
        table_name="conversation_attachments",
    )
    op.drop_table("conversation_attachments")
