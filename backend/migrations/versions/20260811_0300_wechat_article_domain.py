"""Add structured WeChat article working copies, image slots, and draft mappings.

Revision ID: 20260811_0300
Revises: 20260811_0260
Create Date: 2026-08-11 03:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260811_0300"
down_revision: str | None = "20260811_0260"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_DELIVERABLE_TYPES = (
    "positioning_strategy",
    "topic_plan",
    "publish_calendar",
    "publish_package",
    "video_script",
    "art_prompt",
    "video_asset",
    "edited_video",
    "review_report",
    "ad_plan",
    "cs_record",
)
_WECHAT_DELIVERABLE_TYPES = ("wechat_article", "wechat_image_plan", "wechat_rendered_article")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE platform ADD VALUE IF NOT EXISTS 'wechat_official_account'")
            for value in _WECHAT_DELIVERABLE_TYPES:
                op.execute(f"ALTER TYPE deliverable_type ADD VALUE IF NOT EXISTS '{value}'")

    op.create_unique_constraint("uq_content_items_id_account", "content_items", ["id", "account_id"])
    op.create_table(
        "article_working_copies",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("content_item_id", BigIntPK, nullable=False),
        sa.Column("based_on_deliverable_id", BigIntPK, nullable=True),
        sa.Column("document", JSONVariant, nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("updated_by_id", BigIntPK, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("lock_version > 0", name="ck_article_working_copy_lock_version_positive"),
        sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["based_on_deliverable_id"], ["deliverables.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("content_item_id", name="uq_article_working_copy_content_item"),
    )
    op.create_index("ix_article_working_copies_content_item_id", "article_working_copies", ["content_item_id"])
    slot_status = sa.Enum(
        "planned", "generating", "ready", "selected", "failed", name="article_image_slot_status"
    )
    op.create_table(
        "article_image_slots",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("content_item_id", BigIntPK, nullable=False),
        sa.Column("stable_key", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=300), nullable=False),
        sa.Column("placement_after_block_id", sa.String(length=128), nullable=True),
        sa.Column("aspect_ratio", sa.String(length=32), nullable=False),
        sa.Column("visual_brief", sa.Text(), nullable=False),
        sa.Column("prompt_internal", sa.Text(), nullable=True),
        sa.Column("status", slot_status, server_default="planned", nullable=False),
        sa.Column("selected_material_id", BigIntPK, nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("lock_version > 0", name="ck_article_image_slot_lock_version_positive"),
        sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["selected_material_id"], ["material_assets.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("content_item_id", "stable_key", name="uq_article_image_slot_stable_key"),
    )
    op.create_index("ix_article_image_slots_content_item_id", "article_image_slots", ["content_item_id"])
    op.create_index("ix_article_image_slots_selected_material_id", "article_image_slots", ["selected_material_id"])
    op.create_table(
        "wechat_draft_mappings",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("content_item_id", BigIntPK, nullable=False),
        sa.Column("media_id", sa.String(length=256), nullable=False),
        sa.Column("remote_hash", sa.String(length=128), nullable=True),
        sa.Column("last_synced_deliverable_id", BigIntPK, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["content_item_id", "account_id"],
            ["content_items.id", "content_items.account_id"],
            name="fk_wechat_draft_mapping_content_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "org_id"],
            ["accounts.id", "accounts.org_id"],
            name="fk_wechat_draft_mapping_account_org",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["last_synced_deliverable_id"], ["deliverables.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("org_id", "account_id", "content_item_id", name="uq_wechat_draft_mapping_scope"),
    )
    op.create_index("ix_wechat_draft_mappings_org_id", "wechat_draft_mappings", ["org_id"])
    op.create_index("ix_wechat_draft_mappings_account_id", "wechat_draft_mappings", ["account_id"])
    op.create_index("ix_wechat_draft_mappings_content_item_id", "wechat_draft_mappings", ["content_item_id"])


def downgrade() -> None:
    op.drop_table("wechat_draft_mappings")
    op.drop_table("article_image_slots")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE article_image_slot_status")
    op.drop_table("article_working_copies")
    op.drop_constraint("uq_content_items_id_account", "content_items", type_="unique")

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM deliverables
                WHERE type::text IN ('wechat_article', 'wechat_image_plan', 'wechat_rendered_article')
            ) OR EXISTS (
                SELECT 1 FROM deliverable_acceptances
                WHERE deliverable_type::text IN (
                    'wechat_article', 'wechat_image_plan', 'wechat_rendered_article'
                )
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade while WeChat article deliverables or acceptances exist';
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE deliverables ALTER COLUMN type TYPE text USING type::text")
    op.execute(
        "ALTER TABLE deliverable_acceptances ALTER COLUMN deliverable_type "
        "TYPE text USING deliverable_type::text"
    )
    op.execute("DROP TYPE deliverable_type")
    values = ", ".join(f"'{value}'" for value in _OLD_DELIVERABLE_TYPES)
    op.execute(f"CREATE TYPE deliverable_type AS ENUM ({values})")
    op.execute(
        "ALTER TABLE deliverables ALTER COLUMN type TYPE deliverable_type USING type::deliverable_type"
    )
    op.execute(
        "ALTER TABLE deliverable_acceptances ALTER COLUMN deliverable_type "
        "TYPE deliverable_type USING deliverable_type::deliverable_type"
    )
