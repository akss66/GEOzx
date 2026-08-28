"""Add encrypted WeChat Open Platform component credentials.

Revision ID: 20260811_0100
Revises: 20260805_0300
Create Date: 2026-08-11 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260811_0100"
down_revision: str | None = "20260805_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wechat_component_credentials",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("platform_integration_id", BigIntPK, nullable=False),
        sa.Column("component_verify_ticket_encrypted", sa.Text(), nullable=True),
        sa.Column("ticket_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("component_access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["platform_integration_id"], ["platform_integrations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("platform_integration_id", name="uq_wechat_component_integration"),
    )


def downgrade() -> None:
    op.drop_table("wechat_component_credentials")
