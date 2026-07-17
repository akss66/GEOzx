"""encrypted platform account tokens

Revision ID: 20260716_0100
Revises: 20260706_0200
Create Date: 2026-07-16 10:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0100"
down_revision: str | None = "20260706_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_account_auths",
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "platform_account_auths",
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_account_auths", "refresh_token_encrypted")
    op.drop_column("platform_account_auths", "access_token_encrypted")
