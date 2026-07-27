"""Add the AI COO operating semantics ledgers.

Revision ID: 20260727_0300
Revises: 20260727_0200
Create Date: 2026-07-27 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260727_0300"
down_revision: str | None = "20260727_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def _scope_columns(*, task_nullable: bool = False) -> tuple[sa.Column, ...]:
    return (
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("task_id", BigIntPK, nullable=task_nullable),
        sa.Column("run_id", BigIntPK, nullable=True),
        sa.Column("client_id", BigIntPK, nullable=True),
        sa.Column("project_id", BigIntPK, nullable=True),
        sa.Column("account_id", BigIntPK, nullable=True),
    )


def _scope_foreign_keys(*, task_ondelete: str = "CASCADE") -> tuple[sa.ForeignKeyConstraint, ...]:
    return (
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["brain_tasks.id"], ondelete=task_ondelete),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
    )


def _create_scope_indexes(table_name: str, *, include_task: bool = True) -> None:
    columns = ["org_id", "run_id", "client_id", "project_id", "account_id"]
    if include_task:
        columns.insert(1, "task_id")
    for column in columns:
        op.create_index(op.f(f"ix_{table_name}_{column}"), table_name, [column])


def upgrade() -> None:
    op.create_table(
        "strategy_plans",
        sa.Column("id", BigIntPK, primary_key=True),
        *_scope_columns(),
        sa.Column("created_by_id", BigIntPK, nullable=True),
        sa.Column("status", sa.String(length=40), server_default="draft", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column(
            "situation_snapshot",
            JSONVariant,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("strategy", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("kpis", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("risks", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("evidence_refs", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("rationale_summary", sa.Text(), server_default="", nullable=False),
        sa.Column("prompt_id", sa.String(length=120), nullable=True),
        sa.Column("prompt_version", sa.String(length=80), nullable=True),
        sa.Column("prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("schema_version", sa.String(length=40), server_default="1.0", nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_scope_foreign_keys(),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("task_id", "version", name="uq_strategy_plans_task_version"),
    )
    _create_scope_indexes("strategy_plans")
    op.create_index(op.f("ix_strategy_plans_created_by_id"), "strategy_plans", ["created_by_id"])
    op.create_index(op.f("ix_strategy_plans_status"), "strategy_plans", ["status"])

    op.create_table(
        "decision_traces",
        sa.Column("id", BigIntPK, primary_key=True),
        *_scope_columns(),
        sa.Column("trace_key", sa.String(length=160), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("evidence_refs", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("alternatives", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("selected_option", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("decision_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("action_summary", sa.Text(), server_default="", nullable=False),
        sa.Column("outcome", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="decided", nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_scope_foreign_keys(),
        sa.UniqueConstraint("task_id", "trace_key", name="uq_decision_traces_task_key"),
    )
    _create_scope_indexes("decision_traces")
    op.create_index(op.f("ix_decision_traces_status"), "decision_traces", ["status"])

    op.create_table(
        "reflection_records",
        sa.Column("id", BigIntPK, primary_key=True),
        *_scope_columns(),
        sa.Column(
            "status",
            sa.String(length=40),
            server_default="pending_observation",
            nullable=False,
        ),
        sa.Column("goal_snapshot", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("expected_outcome", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("observed_outcome", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("evidence_refs", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("diagnosis", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("conclusion", sa.Text(), server_default="", nullable=False),
        sa.Column("next_strategy", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "experience_candidates",
            JSONVariant,
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_scope_foreign_keys(),
        sa.UniqueConstraint("task_id", "run_id", name="uq_reflection_records_task_run"),
    )
    _create_scope_indexes("reflection_records")
    op.create_index(op.f("ix_reflection_records_status"), "reflection_records", ["status"])

    op.create_table(
        "experience_memories",
        sa.Column("id", BigIntPK, primary_key=True),
        *_scope_columns(task_nullable=True),
        sa.Column("reflection_id", BigIntPK, nullable=True),
        sa.Column("verified_by_id", BigIntPK, nullable=True),
        sa.Column("status", sa.String(length=40), server_default="candidate", nullable=False),
        sa.Column("industry", sa.String(length=160), server_default="", nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("condition", sa.Text(), server_default="", nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), server_default="0", nullable=False),
        sa.Column("source_refs", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "verification_method",
            sa.String(length=40),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("verification_note", sa.Text(), server_default="", nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_scope_foreign_keys(task_ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["reflection_id"], ["reflection_records.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["verified_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_experience_memories_confidence",
        ),
    )
    _create_scope_indexes("experience_memories")
    op.create_index(
        op.f("ix_experience_memories_reflection_id"),
        "experience_memories",
        ["reflection_id"],
    )
    op.create_index(
        op.f("ix_experience_memories_verified_by_id"),
        "experience_memories",
        ["verified_by_id"],
    )
    op.create_index(op.f("ix_experience_memories_status"), "experience_memories", ["status"])

    op.create_table(
        "agent_quality_scores",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("task_id", BigIntPK, nullable=False),
        sa.Column("run_id", BigIntPK, nullable=True),
        sa.Column("invocation_id", BigIntPK, nullable=True),
        sa.Column("deliverable_id", BigIntPK, nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("dimensions", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("issues", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("suggestions", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("passed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("iteration", sa.Integer(), server_default="0", nullable=False),
        sa.Column("evidence_refs", JSONVariant, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("critic_prompt_id", sa.String(length=120), nullable=True),
        sa.Column("critic_prompt_version", sa.String(length=80), nullable=True),
        sa.Column("critic_prompt_hash", sa.String(length=64), nullable=True),
        sa.Column("critic_model", sa.String(length=160), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["brain_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["invocation_id"], ["agent_invocations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["deliverable_id"], ["deliverables.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_agent_quality_scores_score",
        ),
        sa.CheckConstraint(
            "iteration >= 0 AND iteration <= 2",
            name="ck_agent_quality_scores_iteration",
        ),
        sa.UniqueConstraint(
            "task_id",
            "invocation_id",
            "iteration",
            name="uq_agent_quality_scores_invocation_iteration",
        ),
    )
    for column in (
        "org_id",
        "task_id",
        "run_id",
        "invocation_id",
        "deliverable_id",
        "passed",
    ):
        op.create_index(
            op.f(f"ix_agent_quality_scores_{column}"),
            "agent_quality_scores",
            [column],
        )


def downgrade() -> None:
    op.drop_table("agent_quality_scores")
    op.drop_table("experience_memories")
    op.drop_table("reflection_records")
    op.drop_table("decision_traces")
    op.drop_table("strategy_plans")
