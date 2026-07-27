"""Add complete account-to-client bindings.

Revision ID: 20260723_0200
Revises: 20260723_0100
Create Date: 2026-07-23 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK

revision: str = "20260723_0200"
down_revision: str | None = "20260723_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_clients",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("client_id", BigIntPK, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "client_id",
            name="uq_account_clients_account_client",
        ),
    )
    op.create_index(
        op.f("ix_account_clients_account_id"),
        "account_clients",
        ["account_id"],
    )
    op.create_index(
        op.f("ix_account_clients_client_id"),
        "account_clients",
        ["client_id"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO account_clients
                (account_id, client_id, created_at, updated_at)
            SELECT id, client_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM accounts
            WHERE client_id IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_account_clients_client_id"), table_name="account_clients")
    op.drop_index(op.f("ix_account_clients_account_id"), table_name="account_clients")
    op.drop_table("account_clients")
