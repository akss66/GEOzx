"""Persist versioned Skill executions and runtime provenance.

Revision ID: 20260728_0200
Revises: 20260728_0175
Create Date: 2026-07-28 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0200"
down_revision: str | None = "20260728_0175"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_runtime_provenance(table_name: str) -> None:
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(
            sa.Column("skill_run_id", sa.BigInteger(), nullable=True)
        )
        batch_op.add_column(sa.Column("thread_id", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("turn_id", sa.BigInteger(), nullable=True))
        batch_op.create_foreign_key(
            f"fk_{table_name}_skill_run_id_skill_runs",
            "skill_runs",
            ["skill_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            f"fk_{table_name}_thread_id_conversation_threads",
            "conversation_threads",
            ["thread_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            f"fk_{table_name}_turn_id_conversation_turns",
            "conversation_turns",
            ["turn_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            f"ix_{table_name}_skill_run_id",
            ["skill_run_id"],
            unique=False,
        )
        batch_op.create_index(
            f"ix_{table_name}_thread_id",
            ["thread_id"],
            unique=False,
        )
        batch_op.create_index(
            f"ix_{table_name}_turn_id",
            ["turn_id"],
            unique=False,
        )


def _drop_runtime_provenance(table_name: str) -> None:
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.drop_index(f"ix_{table_name}_turn_id")
        batch_op.drop_index(f"ix_{table_name}_thread_id")
        batch_op.drop_index(f"ix_{table_name}_skill_run_id")
        batch_op.drop_constraint(
            f"fk_{table_name}_turn_id_conversation_turns",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            f"fk_{table_name}_thread_id_conversation_threads",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            f"fk_{table_name}_skill_run_id_skill_runs",
            type_="foreignkey",
        )
        batch_op.drop_column("turn_id")
        batch_op.drop_column("thread_id")
        batch_op.drop_column("skill_run_id")


def upgrade() -> None:
    op.create_table(
        "skill_runs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("turn_id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("skill_code", sa.String(length=120), nullable=False),
        sa.Column("skill_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("output_snapshot", sa.JSON(), nullable=False),
        sa.Column("quality_score", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["orgs.id"],
            name="fk_skill_runs_org_id_orgs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["conversation_threads.id"],
            name="fk_skill_runs_thread_id_conversation_threads",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["conversation_turns.id"],
            name="fk_skill_runs_turn_id_conversation_turns",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_skill_runs_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["brain_tasks.id"],
            name="fk_skill_runs_task_id_brain_tasks",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id", "org_id"],
            ["conversation_threads.id", "conversation_threads.org_id"],
            name="fk_skill_runs_thread_org",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id", "thread_id", "org_id"],
            [
                "conversation_turns.id",
                "conversation_turns.thread_id",
                "conversation_turns.org_id",
            ],
            name="fk_skill_runs_turn_thread_org",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_skill_runs_run_idempotency",
        ),
    )
    op.create_index("ix_skill_runs_run_id", "skill_runs", ["run_id"], unique=False)
    op.create_index("ix_skill_runs_task_id", "skill_runs", ["task_id"], unique=False)
    op.create_index(
        "ix_skill_runs_org_status",
        "skill_runs",
        ["org_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_skill_runs_thread_created",
        "skill_runs",
        ["thread_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_skill_runs_turn_skill",
        "skill_runs",
        ["turn_id", "skill_code"],
        unique=False,
    )

    _add_runtime_provenance("agent_invocations")
    _add_runtime_provenance("agent_tool_calls")


def downgrade() -> None:
    _drop_runtime_provenance("agent_tool_calls")
    _drop_runtime_provenance("agent_invocations")

    op.drop_index("ix_skill_runs_turn_skill", table_name="skill_runs")
    op.drop_index("ix_skill_runs_thread_created", table_name="skill_runs")
    op.drop_index("ix_skill_runs_org_status", table_name="skill_runs")
    op.drop_index("ix_skill_runs_task_id", table_name="skill_runs")
    op.drop_index("ix_skill_runs_run_id", table_name="skill_runs")
    op.drop_table("skill_runs")
