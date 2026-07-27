"""Add durable and idempotent agent run records.

Revision ID: 20260721_0100
Revises: 20260720_0400
Create Date: 2026-07-21 11:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260721_0100"
down_revision: str | None = "20260720_0400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("requested_by_id", BigIntPK, nullable=False),
        sa.Column("task_id", BigIntPK, nullable=True),
        sa.Column("client_message_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=40), server_default="claimed", nullable=False),
        sa.Column("phase", sa.String(length=80), server_default="request", nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("request_payload", JSONVariant, nullable=False),
        sa.Column("result_payload", JSONVariant, nullable=False),
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
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["brain_tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "org_id",
            "requested_by_id",
            "client_message_id",
            name="uq_agent_run_request",
        ),
    )
    op.create_index(op.f("ix_agent_runs_org_id"), "agent_runs", ["org_id"])
    op.create_index(
        op.f("ix_agent_runs_requested_by_id"), "agent_runs", ["requested_by_id"]
    )
    op.create_index(op.f("ix_agent_runs_task_id"), "agent_runs", ["task_id"])
    op.create_index(op.f("ix_agent_runs_status"), "agent_runs", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_runs_status"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_task_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_requested_by_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_org_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
