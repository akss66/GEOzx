"""brain models

Revision ID: 84b1d2c3e4f5
Revises: 7b9c2d1e5f00
Create Date: 2026-07-01 01:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "84b1d2c3e4f5"
down_revision: str | None = "7b9c2d1e5f00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

bigint_pk = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "brain_tasks",
        sa.Column("id", bigint_pk, nullable=False),
        sa.Column("org_id", bigint_pk, nullable=False),
        sa.Column("content_item_id", bigint_pk, nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "content_creation",
                "account_diagnosis",
                "review_optimization",
                "matrix_distribution",
                name="brain_task_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "pending_confirmation",
                "running",
                "pending_acceptance",
                "completed",
                "failed",
                name="brain_task_status",
            ),
            nullable=False,
        ),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("current_focus", sa.String(length=500), nullable=False),
        sa.Column("risk_count", sa.Integer(), nullable=False),
        sa.Column("context_closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["content_item_id"], ["content_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_brain_tasks_content_item_id"),
        "brain_tasks",
        ["content_item_id"],
        unique=False,
    )
    op.create_index(op.f("ix_brain_tasks_org_id"), "brain_tasks", ["org_id"], unique=False)
    op.create_index(op.f("ix_brain_tasks_status"), "brain_tasks", ["status"], unique=False)

    op.create_table(
        "task_briefs",
        sa.Column("id", bigint_pk, nullable=False),
        sa.Column("task_id", bigint_pk, nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("project_id", bigint_pk, nullable=True),
        sa.Column("project_name", sa.String(length=200), nullable=True),
        sa.Column("account_group_id", bigint_pk, nullable=True),
        sa.Column("account_group_name", sa.String(length=200), nullable=True),
        sa.Column("platforms", sa.JSON(), nullable=False),
        sa.Column("cycle", sa.String(length=120), nullable=False),
        sa.Column("budget", sa.Numeric(12, 2), nullable=True),
        sa.Column("content_goal", sa.Text(), nullable=False),
        sa.Column("risk_constraints", sa.JSON(), nullable=False),
        sa.Column("expected_outputs", sa.JSON(), nullable=False),
        sa.Column("confirmation_actions", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["account_group_id"], ["account_groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["brain_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index(op.f("ix_task_briefs_task_id"), "task_briefs", ["task_id"], unique=False)

    op.create_table(
        "orchestration_plans",
        sa.Column("id", bigint_pk, nullable=False),
        sa.Column("task_id", bigint_pk, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("quality_gates", sa.JSON(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("requires_human_confirmation", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["task_id"], ["brain_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index(
        op.f("ix_orchestration_plans_task_id"),
        "orchestration_plans",
        ["task_id"],
        unique=False,
    )

    op.create_table(
        "agent_invocations",
        sa.Column("id", bigint_pk, nullable=False),
        sa.Column("task_id", bigint_pk, nullable=False),
        sa.Column("agent_code", sa.String(length=64), nullable=False),
        sa.Column("agent_name", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "running", "done", "failed", "blocked", name="agent_invocation_status"),
            nullable=False,
        ),
        sa.Column("input_summary", sa.Text(), nullable=False),
        sa.Column("output_summary", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("upstream", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["task_id"], ["brain_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_invocations_agent_code"),
        "agent_invocations",
        ["agent_code"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_invocations_status"), "agent_invocations", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_agent_invocations_task_id"), "agent_invocations", ["task_id"], unique=False
    )

    op.create_table(
        "deliverable_acceptances",
        sa.Column("id", bigint_pk, nullable=False),
        sa.Column("task_id", bigint_pk, nullable=False),
        sa.Column("deliverable_id", bigint_pk, nullable=True),
        sa.Column("agent_code", sa.String(length=64), nullable=False),
        sa.Column("agent_name", sa.String(length=120), nullable=False),
        sa.Column("deliverable_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("acceptance_items", sa.JSON(), nullable=False),
        sa.Column("history_versions", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "rerun_requested",
                name="deliverable_acceptance_status",
            ),
            nullable=False,
        ),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column(
            "rerun_scope",
            sa.Enum("current_agent", "upstream", "downstream", "full_chain", name="rerun_scope"),
            nullable=True,
        ),
        sa.Column("brain_rejudge_summary", sa.Text(), nullable=True),
        sa.Column("brain_rejudge_basis", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["deliverable_id"], ["deliverables.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["brain_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_deliverable_acceptances_status"),
        "deliverable_acceptances",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_deliverable_acceptances_task_id"),
        "deliverable_acceptances",
        ["task_id"],
        unique=False,
    )

    op.create_table(
        "automation_policies",
        sa.Column("id", bigint_pk, nullable=False),
        sa.Column("org_id", bigint_pk, nullable=False),
        sa.Column("project_id", bigint_pk, nullable=True),
        sa.Column("account_group_id", bigint_pk, nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("action_type", sa.String(length=120), nullable=False),
        sa.Column("level", sa.Enum("manual", "confirm", "auto", name="automation_level"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["account_group_id"], ["account_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_automation_policies_org_id"), "automation_policies", ["org_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_automation_policies_org_id"), table_name="automation_policies")
    op.drop_table("automation_policies")
    op.drop_index(
        op.f("ix_deliverable_acceptances_task_id"), table_name="deliverable_acceptances"
    )
    op.drop_index(
        op.f("ix_deliverable_acceptances_status"), table_name="deliverable_acceptances"
    )
    op.drop_table("deliverable_acceptances")
    op.drop_index(op.f("ix_agent_invocations_task_id"), table_name="agent_invocations")
    op.drop_index(op.f("ix_agent_invocations_status"), table_name="agent_invocations")
    op.drop_index(op.f("ix_agent_invocations_agent_code"), table_name="agent_invocations")
    op.drop_table("agent_invocations")
    op.drop_index(op.f("ix_orchestration_plans_task_id"), table_name="orchestration_plans")
    op.drop_table("orchestration_plans")
    op.drop_index(op.f("ix_task_briefs_task_id"), table_name="task_briefs")
    op.drop_table("task_briefs")
    op.drop_index(op.f("ix_brain_tasks_status"), table_name="brain_tasks")
    op.drop_index(op.f("ix_brain_tasks_org_id"), table_name="brain_tasks")
    op.drop_index(op.f("ix_brain_tasks_content_item_id"), table_name="brain_tasks")
    op.drop_table("brain_tasks")
    op.execute("DROP TYPE IF EXISTS automation_level")
    op.execute("DROP TYPE IF EXISTS rerun_scope")
    op.execute("DROP TYPE IF EXISTS deliverable_acceptance_status")
    op.execute("DROP TYPE IF EXISTS agent_invocation_status")
    op.execute("DROP TYPE IF EXISTS brain_task_status")
    op.execute("DROP TYPE IF EXISTS brain_task_type")
