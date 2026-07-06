"""optimization_suggestions

Revision ID: 7b9c2d1e5f00
Revises: eeea13a2d75d
Create Date: 2026-07-01 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7b9c2d1e5f00"
down_revision: str | None = "eeea13a2d75d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "optimization_suggestions",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("org_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column(
            "content_item_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column(
            "source_deliverable_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=True,
        ),
        sa.Column("target_stage", sa.String(length=64), nullable=True),
        sa.Column("suggestion", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "suggested",
                "accepted",
                "verified",
                name="optimization_suggestion_status",
            ),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_deliverable_id"], ["deliverables.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_deliverable_id",
            "suggestion",
            name="uq_optimization_suggestion_source_text",
        ),
    )
    op.create_index(
        op.f("ix_optimization_suggestions_content_item_id"),
        "optimization_suggestions",
        ["content_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_optimization_suggestions_org_id"),
        "optimization_suggestions",
        ["org_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_optimization_suggestions_source_deliverable_id"),
        "optimization_suggestions",
        ["source_deliverable_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_optimization_suggestions_status"),
        "optimization_suggestions",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_optimization_suggestions_status"), table_name="optimization_suggestions")
    op.drop_index(
        op.f("ix_optimization_suggestions_source_deliverable_id"),
        table_name="optimization_suggestions",
    )
    op.drop_index(op.f("ix_optimization_suggestions_org_id"), table_name="optimization_suggestions")
    op.drop_index(
        op.f("ix_optimization_suggestions_content_item_id"),
        table_name="optimization_suggestions",
    )
    op.drop_table("optimization_suggestions")
    op.execute("DROP TYPE IF EXISTS optimization_suggestion_status")
