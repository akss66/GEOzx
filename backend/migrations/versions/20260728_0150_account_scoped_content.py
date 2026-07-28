"""Allow ContentItem records scoped directly to an account.

Revision ID: 20260728_0150
Revises: 20260728_0100
Create Date: 2026-07-28 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260728_0150"
down_revision: str | None = "20260728_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("content_items") as batch_op:
        batch_op.alter_column(
            "project_id",
            existing_type=BigIntPK,
            existing_nullable=False,
            nullable=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    has_account_scoped_content = connection.execute(
        sa.text("SELECT 1 FROM content_items WHERE project_id IS NULL LIMIT 1")
    ).first()
    if has_account_scoped_content is not None:
        raise RuntimeError(
            "Cannot downgrade while content_items.project_id IS NULL account-scoped rows exist."
        )

    with op.batch_alter_table("content_items") as batch_op:
        batch_op.alter_column(
            "project_id",
            existing_type=BigIntPK,
            existing_nullable=True,
            nullable=False,
        )
