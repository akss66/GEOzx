"""Persist identity governance controls.

Revision ID: 20260720_0100
Revises: 20260717_0300
Create Date: 2026-07-20 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260720_0100"
down_revision: str | None = "20260717_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "account_scope_mode",
            sa.String(length=32),
            nullable=False,
            server_default="all_accessible",
        ),
    )
    op.create_table(
        "admin_security_credentials",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("user_id", BigIntPK, nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "account_memberships",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("user_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "account_id"),
    )
    for table_name in ("brain_tasks", "content_items", "llm_calls"):
        op.add_column(table_name, sa.Column("created_by_id", BigIntPK, nullable=True))
        op.create_foreign_key(
            f"fk_{table_name}_created_by_id",
            table_name,
            "users",
            ["created_by_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    for table_name in ("llm_calls", "content_items", "brain_tasks"):
        op.drop_constraint(f"fk_{table_name}_created_by_id", table_name, type_="foreignkey")
        op.drop_column(table_name, "created_by_id")
    op.drop_table("account_memberships")
    op.drop_table("admin_security_credentials")
    op.drop_column("users", "account_scope_mode")
