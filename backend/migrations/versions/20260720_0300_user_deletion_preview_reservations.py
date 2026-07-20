"""Add atomic deletion preview reservations.

Revision ID: 20260720_0300
Revises: 20260720_0200
Create Date: 2026-07-20 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260720_0300"
down_revision: str | None = "20260720_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_deletion_preview_reservations",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("organization_id", BigIntPK, nullable=False),
        sa.Column("operation_id", sa.String(length=64), nullable=False),
        sa.Column(
            "reserved_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["orgs.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "operation_id",
            name="uq_user_deletion_preview_reservations_org_operation",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_deletion_preview_reservations")
