"""Allow account deletion to cascade through conversation runtime scope keys.

Revision ID: 20260805_0400
Revises: 20260805_0300
Create Date: 2026-08-24 11:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260805_0400"
down_revision: str | None = "20260805_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CASCADE_FOREIGN_KEYS = (
    (
        "conversation_turns",
        "fk_conversation_turn_thread_org",
        ["thread_id", "org_id"],
        "conversation_threads",
        ["id", "org_id"],
    ),
    (
        "agent_runs",
        "fk_agent_runs_thread_org",
        ["thread_id", "org_id"],
        "conversation_threads",
        ["id", "org_id"],
    ),
    (
        "agent_runs",
        "fk_agent_runs_turn_thread_org",
        ["turn_id", "thread_id", "org_id"],
        "conversation_turns",
        ["id", "thread_id", "org_id"],
    ),
    (
        "skill_runs",
        "fk_skill_runs_thread_org",
        ["thread_id", "org_id"],
        "conversation_threads",
        ["id", "org_id"],
    ),
    (
        "skill_runs",
        "fk_skill_runs_turn_thread_org",
        ["turn_id", "thread_id", "org_id"],
        "conversation_turns",
        ["id", "thread_id", "org_id"],
    ),
    (
        "skill_runs",
        "fk_skill_runs_run_thread_turn_org",
        ["run_id", "thread_id", "turn_id", "org_id"],
        "agent_runs",
        ["id", "thread_id", "turn_id", "org_id"],
    ),
    (
        "skill_runs",
        "fk_skill_runs_run_task_thread_turn_org",
        ["run_id", "task_id", "thread_id", "turn_id", "org_id"],
        "agent_runs",
        ["id", "task_id", "thread_id", "turn_id", "org_id"],
    ),
)


def _replace_constraints(*, ondelete: str | None) -> None:
    for table, name, local_columns, remote_table, remote_columns in _CASCADE_FOREIGN_KEYS:
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            name,
            table,
            remote_table,
            local_columns,
            remote_columns,
            ondelete=ondelete,
        )


def upgrade() -> None:
    _replace_constraints(ondelete="CASCADE")


def downgrade() -> None:
    _replace_constraints(ondelete=None)
