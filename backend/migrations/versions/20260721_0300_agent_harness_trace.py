"""Add specialist step identity and model-call trace metadata.

Revision ID: 20260721_0300
Revises: 20260721_0200
Create Date: 2026-07-21 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260721_0300"
down_revision: str | None = "20260721_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_invocations",
        sa.Column("run_id", BigIntPK, nullable=True),
    )
    op.add_column(
        "agent_invocations",
        sa.Column("step_key", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "agent_invocations",
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_foreign_key(
        "fk_agent_invocations_run_id_agent_runs",
        "agent_invocations",
        "agent_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_agent_invocations_run_id"),
        "agent_invocations",
        ["run_id"],
    )
    op.create_unique_constraint(
        "uq_agent_invocation_run_step",
        "agent_invocations",
        ["run_id", "step_key", "attempt"],
    )

    op.add_column("llm_calls", sa.Column("task_id", BigIntPK, nullable=True))
    op.add_column("llm_calls", sa.Column("invocation_id", BigIntPK, nullable=True))
    op.add_column("llm_calls", sa.Column("trace_id", sa.String(length=120), nullable=True))
    op.add_column("llm_calls", sa.Column("prompt_id", sa.String(length=120), nullable=True))
    op.add_column("llm_calls", sa.Column("prompt_version", sa.String(length=40), nullable=True))
    op.add_column("llm_calls", sa.Column("prompt_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "llm_calls",
        sa.Column("prompt_schema_version", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "llm_calls",
        sa.Column(
            "scope",
            JSONVariant,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "llm_calls",
        sa.Column(
            "budget",
            JSONVariant,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_llm_calls_task_id_brain_tasks",
        "llm_calls",
        "brain_tasks",
        ["task_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_llm_calls_invocation_id_agent_invocations",
        "llm_calls",
        "agent_invocations",
        ["invocation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for column in ("task_id", "invocation_id", "trace_id", "prompt_id"):
        op.create_index(op.f(f"ix_llm_calls_{column}"), "llm_calls", [column])


def downgrade() -> None:
    for column in ("prompt_id", "trace_id", "invocation_id", "task_id"):
        op.drop_index(op.f(f"ix_llm_calls_{column}"), table_name="llm_calls")
    op.drop_constraint(
        "fk_llm_calls_invocation_id_agent_invocations",
        "llm_calls",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_llm_calls_task_id_brain_tasks",
        "llm_calls",
        type_="foreignkey",
    )
    for column in (
        "budget",
        "scope",
        "prompt_schema_version",
        "prompt_hash",
        "prompt_version",
        "prompt_id",
        "trace_id",
        "invocation_id",
        "task_id",
    ):
        op.drop_column("llm_calls", column)

    op.drop_constraint(
        "uq_agent_invocation_run_step",
        "agent_invocations",
        type_="unique",
    )
    op.drop_index(op.f("ix_agent_invocations_run_id"), table_name="agent_invocations")
    op.drop_constraint(
        "fk_agent_invocations_run_id_agent_runs",
        "agent_invocations",
        type_="foreignkey",
    )
    op.drop_column("agent_invocations", "attempt")
    op.drop_column("agent_invocations", "step_key")
    op.drop_column("agent_invocations", "run_id")
