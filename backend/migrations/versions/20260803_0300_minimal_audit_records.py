"""Add content-free audit facts for permanent conversation deletion.

Revision ID: 20260803_0300
Revises: 20260803_0200
Create Date: 2026-08-03 16:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import JSONVariant

revision: str = "20260803_0300"
down_revision: str | None = "20260803_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_records",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("amount_usd", sa.Numeric(precision=14, scale=6), nullable=True),
        sa.Column("details", JSONVariant, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "category IN ('approval', 'publish', 'cost')",
            name="ck_audit_records_category",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_audit_records_org_account_category_occurred",
        "audit_records",
        ["org_id", "account_id", "category", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audit_records_org_account_category_occurred",
        table_name="audit_records",
    )
    op.drop_table("audit_records")
