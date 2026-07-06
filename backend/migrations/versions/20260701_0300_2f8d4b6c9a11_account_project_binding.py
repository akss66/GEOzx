"""account project binding

Revision ID: 2f8d4b6c9a11
Revises: 9d2f6a7b8c10
Create Date: 2026-07-01 03:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2f8d4b6c9a11"
down_revision: str | None = "9d2f6a7b8c10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

bigint_pk = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.add_column("accounts", sa.Column("project_id", bigint_pk, nullable=True))
    op.create_index(op.f("ix_accounts_project_id"), "accounts", ["project_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_accounts_project_id_projects"),
        "accounts",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_accounts_project_id_projects"), "accounts", type_="foreignkey")
    op.drop_index(op.f("ix_accounts_project_id"), table_name="accounts")
    op.drop_column("accounts", "project_id")
