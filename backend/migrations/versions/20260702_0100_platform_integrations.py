"""platform integrations

Revision ID: 20260702_0100
Revises: 6c4d8e2f0a13
Create Date: 2026-07-02 01:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260702_0100"
down_revision: str | None = "6c4d8e2f0a13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_integrations",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("client_key", sa.String(length=128), nullable=True),
        sa.Column("client_secret_ref", sa.String(length=256), nullable=True),
        sa.Column("redirect_uri", sa.String(length=500), nullable=True),
        sa.Column("js_sdk_domain", sa.String(length=500), nullable=True),
        sa.Column("auth_status", sa.String(length=32), nullable=False),
        sa.Column("data_sync_status", sa.String(length=32), nullable=False),
        sa.Column("scopes", JSONVariant, nullable=False),
        sa.Column("capabilities", JSONVariant, nullable=False),
        sa.Column("official_docs", JSONVariant, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "platform", name="uq_platform_integration"),
    )
    op.create_index(op.f("ix_platform_integrations_org_id"), "platform_integrations", ["org_id"])

    op.create_table(
        "platform_account_auths",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("external_open_id", sa.String(length=128), nullable=True),
        sa.Column("union_id", sa.String(length=128), nullable=True),
        sa.Column("auth_status", sa.String(length=32), nullable=False),
        sa.Column("data_sync_status", sa.String(length=32), nullable=False),
        sa.Column("scopes", JSONVariant, nullable=False),
        sa.Column("token_secret_ref", sa.String(length=256), nullable=True),
        sa.Column("refresh_secret_ref", sa.String(length=256), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("raw_profile", JSONVariant, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", name="uq_platform_account_auth_account"),
    )
    op.create_index(op.f("ix_platform_account_auths_account_id"), "platform_account_auths", ["account_id"])
    op.create_index(op.f("ix_platform_account_auths_org_id"), "platform_account_auths", ["org_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_platform_account_auths_org_id"), table_name="platform_account_auths")
    op.drop_index(op.f("ix_platform_account_auths_account_id"), table_name="platform_account_auths")
    op.drop_table("platform_account_auths")
    op.drop_index(op.f("ix_platform_integrations_org_id"), table_name="platform_integrations")
    op.drop_table("platform_integrations")
