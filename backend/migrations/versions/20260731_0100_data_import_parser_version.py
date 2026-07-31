"""Version persisted data-import previews for safe parser upgrades.

Revision ID: 20260731_0100
Revises: 20260730_0600
Create Date: 2026-07-31 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260731_0100"
down_revision: str | None = "20260730_0600"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("data_import_batches") as batch_op:
        batch_op.add_column(sa.Column("parser_version", sa.Integer(), nullable=True))

    op.execute(
        sa.text(
            "UPDATE data_import_batches "
            "SET parser_version = 1 "
            "WHERE parser_version IS NULL"
        )
    )

    with op.batch_alter_table("data_import_batches") as batch_op:
        batch_op.alter_column(
            "parser_version",
            existing_type=sa.Integer(),
            nullable=False,
            server_default="1",
        )
        batch_op.create_check_constraint(
            "ck_data_import_batches_parser_version_positive",
            "parser_version >= 1",
        )


def downgrade() -> None:
    with op.batch_alter_table("data_import_batches") as batch_op:
        batch_op.drop_constraint(
            "ck_data_import_batches_parser_version_positive",
            type_="check",
        )
        batch_op.drop_column("parser_version")
