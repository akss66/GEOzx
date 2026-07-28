"""Trace formal results to their source conversation turn and Skill run.

Revision ID: 20260728_0300
Revises: 20260728_0200
Create Date: 2026-07-28 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0300"
down_revision: str | None = "20260728_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE_TABLES = {
    "thread_id": "conversation_threads",
    "turn_id": "conversation_turns",
    "run_id": "agent_runs",
    "skill_run_id": "skill_runs",
}
_TABLE_COLUMNS = {
    "deliverables": tuple(_SOURCE_TABLES),
    "strategy_plans": ("thread_id", "turn_id", "skill_run_id"),
    "decision_traces": ("thread_id", "turn_id", "skill_run_id"),
    "reflection_records": ("thread_id", "turn_id", "skill_run_id"),
    "agent_quality_scores": ("thread_id", "turn_id", "skill_run_id"),
    "events": tuple(_SOURCE_TABLES),
}


def _foreign_key_name(table_name: str, column_name: str) -> str:
    return (
        f"fk_{table_name}_{column_name}_"
        f"{_SOURCE_TABLES[column_name]}"
    )


def _add_provenance(table_name: str, column_names: tuple[str, ...]) -> None:
    with op.batch_alter_table(table_name) as batch_op:
        for column_name in column_names:
            batch_op.add_column(
                sa.Column(column_name, sa.BigInteger(), nullable=True)
            )
            batch_op.create_foreign_key(
                _foreign_key_name(table_name, column_name),
                _SOURCE_TABLES[column_name],
                [column_name],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_index(
                f"ix_{table_name}_{column_name}",
                [column_name],
                unique=False,
            )


def _drop_provenance(table_name: str, column_names: tuple[str, ...]) -> None:
    with op.batch_alter_table(table_name) as batch_op:
        for column_name in reversed(column_names):
            batch_op.drop_index(f"ix_{table_name}_{column_name}")
            batch_op.drop_constraint(
                _foreign_key_name(table_name, column_name),
                type_="foreignkey",
            )
            batch_op.drop_column(column_name)


def upgrade() -> None:
    for table_name, column_names in _TABLE_COLUMNS.items():
        _add_provenance(table_name, column_names)


def downgrade() -> None:
    for table_name, column_names in reversed(tuple(_TABLE_COLUMNS.items())):
        _drop_provenance(table_name, column_names)
