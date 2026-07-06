"""matrix distribution plans

Revision ID: 20260706_0100
Revises: 20260703_0100
Create Date: 2026-07-06 01:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260706_0100"
down_revision: str | None = "20260703_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "matrix_distribution_plans",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("content_item_id", BigIntPK, nullable=True),
        sa.Column("created_by_id", BigIntPK, nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("platforms", JSONVariant, nullable=False),
        sa.Column("account_ids", JSONVariant, nullable=False),
        sa.Column("material_ids", JSONVariant, nullable=False),
        sa.Column("topics", JSONVariant, nullable=False),
        sa.Column("cover_material_id", sa.Integer(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_matrix_distribution_plans_content_item_id"), "matrix_distribution_plans", ["content_item_id"])
    op.create_index(op.f("ix_matrix_distribution_plans_org_id"), "matrix_distribution_plans", ["org_id"])
    op.create_index(op.f("ix_matrix_distribution_plans_status"), "matrix_distribution_plans", ["status"])

    op.create_table(
        "matrix_distribution_items",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("plan_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("material_id", BigIntPK, nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("tool_call_id", BigIntPK, nullable=True),
        sa.Column("publish_package", JSONVariant, nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["material_id"], ["material_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["matrix_distribution_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tool_call_id"], ["agent_tool_calls.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_matrix_distribution_items_account_id"), "matrix_distribution_items", ["account_id"])
    op.create_index(op.f("ix_matrix_distribution_items_material_id"), "matrix_distribution_items", ["material_id"])
    op.create_index(op.f("ix_matrix_distribution_items_org_id"), "matrix_distribution_items", ["org_id"])
    op.create_index(op.f("ix_matrix_distribution_items_plan_id"), "matrix_distribution_items", ["plan_id"])
    op.create_index(op.f("ix_matrix_distribution_items_platform"), "matrix_distribution_items", ["platform"])
    op.create_index(op.f("ix_matrix_distribution_items_status"), "matrix_distribution_items", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_matrix_distribution_items_status"), table_name="matrix_distribution_items")
    op.drop_index(op.f("ix_matrix_distribution_items_platform"), table_name="matrix_distribution_items")
    op.drop_index(op.f("ix_matrix_distribution_items_plan_id"), table_name="matrix_distribution_items")
    op.drop_index(op.f("ix_matrix_distribution_items_org_id"), table_name="matrix_distribution_items")
    op.drop_index(op.f("ix_matrix_distribution_items_material_id"), table_name="matrix_distribution_items")
    op.drop_index(op.f("ix_matrix_distribution_items_account_id"), table_name="matrix_distribution_items")
    op.drop_table("matrix_distribution_items")
    op.drop_index(op.f("ix_matrix_distribution_plans_status"), table_name="matrix_distribution_plans")
    op.drop_index(op.f("ix_matrix_distribution_plans_org_id"), table_name="matrix_distribution_plans")
    op.drop_index(op.f("ix_matrix_distribution_plans_content_item_id"), table_name="matrix_distribution_plans")
    op.drop_table("matrix_distribution_plans")
