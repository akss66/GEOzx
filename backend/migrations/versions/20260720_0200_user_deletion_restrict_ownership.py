"""Restrict direct deletion across every protected creator-owned root.

Revision ID: 20260720_0200
Revises: 20260720_0100
Create Date: 2026-07-20 14:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260720_0200"
down_revision: str | None = "20260720_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SQLITE_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
}


def _replace_creator_foreign_key(
    table_name: str,
    *,
    existing_postgresql_name: str,
    existing_sqlite_name: str,
    new_name: str,
    ondelete: str,
    new_sqlite_name: str | None = None,
) -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(
            table_name,
            naming_convention=_SQLITE_NAMING_CONVENTION,
        ) as batch_op:
            batch_op.drop_constraint(existing_sqlite_name, type_="foreignkey")
            batch_op.create_foreign_key(
                new_sqlite_name or new_name,
                "users",
                ["created_by_id"],
                ["id"],
                ondelete=ondelete,
            )
        return
    op.drop_constraint(existing_postgresql_name, table_name, type_="foreignkey")
    op.create_foreign_key(
        new_name,
        table_name,
        "users",
        ["created_by_id"],
        ["id"],
        ondelete=ondelete,
    )


def upgrade() -> None:
    _replace_creator_foreign_key(
        "matrix_distribution_plans",
        existing_postgresql_name="matrix_distribution_plans_created_by_id_fkey",
        existing_sqlite_name="fk_matrix_distribution_plans_created_by_id_users",
        new_name="fk_matrix_distribution_plans_created_by_id",
        ondelete="RESTRICT",
    )
    _replace_creator_foreign_key(
        "knowledge_entries",
        existing_postgresql_name="fk_knowledge_entries_created_by_id",
        existing_sqlite_name="fk_knowledge_entries_created_by_id",
        new_name="fk_knowledge_entries_created_by_id",
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    _replace_creator_foreign_key(
        "knowledge_entries",
        existing_postgresql_name="fk_knowledge_entries_created_by_id",
        existing_sqlite_name="fk_knowledge_entries_created_by_id",
        new_name="fk_knowledge_entries_created_by_id",
        ondelete="SET NULL",
    )
    _replace_creator_foreign_key(
        "matrix_distribution_plans",
        existing_postgresql_name="fk_matrix_distribution_plans_created_by_id",
        existing_sqlite_name="fk_matrix_distribution_plans_created_by_id",
        new_name="matrix_distribution_plans_created_by_id_fkey",
        new_sqlite_name="fk_matrix_distribution_plans_created_by_id_users",
        ondelete="SET NULL",
    )
