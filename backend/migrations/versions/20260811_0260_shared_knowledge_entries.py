"""Represent organization-shared knowledge entries without a client scope.

Revision ID: 20260811_0260
Revises: 20260811_0250
Create Date: 2026-08-11 03:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260811_0260"
down_revision: str | None = "20260811_0250"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_entries", sa.Column("knowledge_base_kind", sa.String(length=40), nullable=True)
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM knowledge_entries entry
                LEFT JOIN knowledge_bases base ON base.id = entry.knowledge_base_id
                WHERE entry.knowledge_base_id IS NOT NULL
                  AND (
                    base.id IS NULL
                    OR base.org_id <> entry.org_id
                    OR base.kind <> 'brand'
                    OR base.client_id IS DISTINCT FROM entry.client_id
                  )
            ) THEN
                RAISE EXCEPTION
                    'cannot migrate inconsistent base-bound knowledge_entries to scoped entries';
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        UPDATE knowledge_entries AS entry
        SET knowledge_base_kind = base.kind
        FROM knowledge_bases AS base
        WHERE entry.knowledge_base_id = base.id
        """
    )
    op.alter_column(
        "knowledge_entries", "client_id", existing_type=BigIntPK, nullable=True
    )
    op.create_check_constraint(
        "ck_knowledge_entries_scope_kind_client",
        "knowledge_entries",
        "(knowledge_base_id IS NULL AND knowledge_base_kind IS NULL "
        "AND client_id IS NOT NULL) OR "
        "(knowledge_base_id IS NOT NULL AND knowledge_base_kind = 'brand' "
        "AND knowledge_base_kind IS NOT NULL AND client_id IS NOT NULL) OR "
        "(knowledge_base_id IS NOT NULL AND knowledge_base_kind = 'organization_shared' "
        "AND knowledge_base_kind IS NOT NULL AND client_id IS NULL)",
    )
    op.create_foreign_key(
        "fk_knowledge_entries_base_org_kind",
        "knowledge_entries",
        "knowledge_bases",
        ["knowledge_base_id", "org_id", "knowledge_base_kind"],
        ["id", "org_id", "kind"],
    )
    op.create_foreign_key(
        "fk_knowledge_entries_base_client",
        "knowledge_entries",
        "knowledge_bases",
        ["knowledge_base_id", "client_id"],
        ["id", "client_id"],
    )
    op.create_index(
        "ix_knowledge_entries_base_scope",
        "knowledge_entries",
        ["knowledge_base_id", "knowledge_base_kind", "org_id", "client_id"],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM knowledge_entries
                WHERE knowledge_base_kind = 'organization_shared'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade while organization_shared knowledge entries exist';
            END IF;
        END $$;
        """
    )
    op.drop_index("ix_knowledge_entries_base_scope", table_name="knowledge_entries")
    op.drop_constraint(
        "fk_knowledge_entries_base_client", "knowledge_entries", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_knowledge_entries_base_org_kind", "knowledge_entries", type_="foreignkey"
    )
    op.drop_constraint(
        "ck_knowledge_entries_scope_kind_client", "knowledge_entries", type_="check"
    )
    op.drop_column("knowledge_entries", "knowledge_base_kind")
    op.alter_column(
        "knowledge_entries", "client_id", existing_type=BigIntPK, nullable=False
    )
