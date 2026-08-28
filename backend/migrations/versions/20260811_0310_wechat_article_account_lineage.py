"""Require account-scoped lineage for mutable WeChat article state.

Revision ID: 20260811_0310
Revises: 20260811_0300
Create Date: 2026-08-11 03:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260811_0310"
down_revision: str | None = "20260811_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM article_working_copies copy
                JOIN content_items content ON content.id = copy.content_item_id
                WHERE content.account_id IS NULL
            ) OR EXISTS (
                SELECT 1
                FROM article_image_slots slot
                JOIN content_items content ON content.id = slot.content_item_id
                WHERE content.account_id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot add WeChat article account lineage for unscoped content items';
            END IF;
        END $$;
        """
    )
    for table_name in ("article_working_copies", "article_image_slots"):
        op.add_column(table_name, sa.Column("account_id", BigIntPK, nullable=True))
        op.execute(
            f"""
            UPDATE {table_name} AS article
            SET account_id = content.account_id
            FROM content_items AS content
            WHERE content.id = article.content_item_id
            """
        )
        op.alter_column(table_name, "account_id", existing_type=BigIntPK, nullable=False)
    op.create_foreign_key(
        "fk_article_working_copy_content_account",
        "article_working_copies",
        "content_items",
        ["content_item_id", "account_id"],
        ["id", "account_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_article_image_slot_content_account",
        "article_image_slots",
        "content_items",
        ["content_item_id", "account_id"],
        ["id", "account_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    for table_name, constraint_name in (
        ("article_image_slots", "fk_article_image_slot_content_account"),
        ("article_working_copies", "fk_article_working_copy_content_account"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")
        op.drop_column(table_name, "account_id")
