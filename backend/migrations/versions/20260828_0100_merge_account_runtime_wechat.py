"""Merge the account runtime and WeChat migration branches.

Revision ID: 20260828_0100
Revises: 20260805_0400, 20260811_0400
Create Date: 2026-08-28 15:45:00.000000
"""

from collections.abc import Sequence


revision: str = "20260828_0100"
down_revision: tuple[str, str] = ("20260805_0400", "20260811_0400")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
