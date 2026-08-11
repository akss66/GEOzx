"""Add organization and brand knowledge base scopes.

Revision ID: 20260811_0200
Revises: 20260811_0100
Create Date: 2026-08-11 02:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260811_0200"
down_revision: str | None = "20260811_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_accounts_id_client", "accounts", ["id", "client_id"])

    op.create_table(
        "knowledge_bases",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("client_id", BigIntPK, nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), server_default="active", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_id", BigIntPK, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(kind = 'brand' AND client_id IS NOT NULL) OR "
            "(kind = 'organization_shared' AND client_id IS NULL)",
            name="ck_knowledge_bases_kind_client_scope",
        ),
        sa.CheckConstraint(
            "kind IN ('brand', 'organization_shared')",
            name="ck_knowledge_bases_kind",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "org_id", "kind", name="uq_knowledge_bases_id_org_kind"),
        sa.UniqueConstraint("id", "client_id", name="uq_knowledge_bases_id_client"),
    )
    for column in ("org_id", "client_id", "status"):
        op.create_index(op.f(f"ix_knowledge_bases_{column}"), "knowledge_bases", [column])

    op.create_table(
        "account_knowledge_bindings",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("knowledge_base_id", BigIntPK, nullable=False),
        sa.Column("knowledge_base_kind", sa.String(length=40), nullable=False),
        sa.Column("client_id", BigIntPK, nullable=True),
        sa.Column("binding_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="active", nullable=False),
        sa.Column("bound_by_id", BigIntPK, nullable=True),
        sa.Column(
            "bound_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "binding_type IN ('primary_brand', 'shared')",
            name="ck_account_knowledge_bindings_type",
        ),
        sa.CheckConstraint(
            "(binding_type = 'primary_brand' AND knowledge_base_kind = 'brand' "
            "AND client_id IS NOT NULL) OR "
            "(binding_type = 'shared' AND knowledge_base_kind = 'organization_shared' "
            "AND client_id IS NULL)",
            name="ck_account_knowledge_bindings_scope_type",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["bound_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["account_id", "org_id"],
            ["accounts.id", "accounts.org_id"],
            name="fk_account_knowledge_bindings_account_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "org_id", "knowledge_base_kind"],
            ["knowledge_bases.id", "knowledge_bases.org_id", "knowledge_bases.kind"],
            name="fk_account_knowledge_bindings_base_org_kind",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "client_id"],
            ["accounts.id", "accounts.client_id"],
            name="fk_account_knowledge_bindings_account_client",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "client_id"],
            ["knowledge_bases.id", "knowledge_bases.client_id"],
            name="fk_account_knowledge_bindings_base_client",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("org_id", "account_id", "knowledge_base_id", "client_id", "status"):
        op.create_index(
            op.f(f"ix_account_knowledge_bindings_{column}"),
            "account_knowledge_bindings",
            [column],
        )
    op.create_index(
        "uq_account_knowledge_bindings_active_primary_brand",
        "account_knowledge_bindings",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("binding_type = 'primary_brand' AND status = 'active'"),
    )

    op.add_column("knowledge_entries", sa.Column("knowledge_base_id", BigIntPK, nullable=True))
    op.add_column(
        "knowledge_entries",
        sa.Column("entry_kind", sa.String(length=40), server_default="document", nullable=False),
    )
    op.add_column(
        "knowledge_entries",
        sa.Column(
            "verification_status", sa.String(length=40), server_default="draft", nullable=False
        ),
    )
    op.add_column("knowledge_entries", sa.Column("verified_at", sa.DateTime(timezone=True)))
    op.add_column("knowledge_entries", sa.Column("verified_by_id", BigIntPK, nullable=True))
    op.add_column("knowledge_entries", sa.Column("source_attachment_id", BigIntPK, nullable=True))
    op.add_column("knowledge_entries", sa.Column("effective_at", sa.DateTime(timezone=True)))
    op.add_column("knowledge_entries", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        "knowledge_entries",
        sa.Column(
            "allowed_for_external_claim", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.create_foreign_key(
        "fk_knowledge_entries_knowledge_base_id",
        "knowledge_entries",
        "knowledge_bases",
        ["knowledge_base_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_knowledge_entries_verified_by_id",
        "knowledge_entries",
        "users",
        ["verified_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_knowledge_entries_source_attachment_id",
        "knowledge_entries",
        "conversation_attachments",
        ["source_attachment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_knowledge_entries_entry_kind",
        "knowledge_entries",
        "entry_kind IN ('document', 'product_fact', 'policy', 'case', 'brand_voice', "
        "'asset_reference')",
    )
    op.create_check_constraint(
        "ck_knowledge_entries_verification_status",
        "knowledge_entries",
        "verification_status IN ('draft', 'verified', 'rejected', 'expired')",
    )
    op.create_check_constraint(
        "ck_knowledge_entries_validity_range",
        "knowledge_entries",
        "effective_at IS NULL OR expires_at IS NULL OR effective_at <= expires_at",
    )
    for column in ("knowledge_base_id", "verification_status", "entry_kind"):
        op.create_index(op.f(f"ix_knowledge_entries_{column}"), "knowledge_entries", [column])


def downgrade() -> None:
    for column in ("entry_kind", "verification_status", "knowledge_base_id"):
        op.drop_index(op.f(f"ix_knowledge_entries_{column}"), table_name="knowledge_entries")
    for constraint in (
        "ck_knowledge_entries_validity_range",
        "ck_knowledge_entries_verification_status",
        "ck_knowledge_entries_entry_kind",
    ):
        op.drop_constraint(constraint, "knowledge_entries", type_="check")
    for constraint in (
        "fk_knowledge_entries_source_attachment_id",
        "fk_knowledge_entries_verified_by_id",
        "fk_knowledge_entries_knowledge_base_id",
    ):
        op.drop_constraint(constraint, "knowledge_entries", type_="foreignkey")
    for column in (
        "allowed_for_external_claim",
        "expires_at",
        "effective_at",
        "source_attachment_id",
        "verified_by_id",
        "verified_at",
        "verification_status",
        "entry_kind",
        "knowledge_base_id",
    ):
        op.drop_column("knowledge_entries", column)

    op.drop_index(
        "uq_account_knowledge_bindings_active_primary_brand",
        table_name="account_knowledge_bindings",
    )
    for column in ("status", "client_id", "knowledge_base_id", "account_id", "org_id"):
        op.drop_index(
            op.f(f"ix_account_knowledge_bindings_{column}"),
            table_name="account_knowledge_bindings",
        )
    op.drop_table("account_knowledge_bindings")

    for column in ("status", "client_id", "org_id"):
        op.drop_index(op.f(f"ix_knowledge_bases_{column}"), table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
    op.drop_constraint("uq_accounts_id_client", "accounts", type_="unique")
