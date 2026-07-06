"""agent tool calls

Revision ID: 20260703_0100
Revises: 20260702_0100
Create Date: 2026-07-03 01:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260703_0100"
down_revision: str | None = "20260702_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_tool_calls",
        sa.Column("id", BigIntPK, nullable=False),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("task_id", BigIntPK, nullable=False),
        sa.Column("invocation_id", BigIntPK, nullable=True),
        sa.Column("module", sa.String(length=64), nullable=False),
        sa.Column("agent_code", sa.String(length=64), nullable=True),
        sa.Column("tool_code", sa.String(length=120), nullable=False),
        sa.Column("tool_name", sa.String(length=180), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("permission_mode", sa.String(length=40), nullable=False),
        sa.Column("requires_human_confirmation", sa.Boolean(), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=False),
        sa.Column("output_summary", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("meta", JSONVariant, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["invocation_id"], ["agent_invocations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["brain_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_tool_calls_agent_code"), "agent_tool_calls", ["agent_code"])
    op.create_index(op.f("ix_agent_tool_calls_invocation_id"), "agent_tool_calls", ["invocation_id"])
    op.create_index(op.f("ix_agent_tool_calls_module"), "agent_tool_calls", ["module"])
    op.create_index(op.f("ix_agent_tool_calls_org_id"), "agent_tool_calls", ["org_id"])
    op.create_index(op.f("ix_agent_tool_calls_status"), "agent_tool_calls", ["status"])
    op.create_index(op.f("ix_agent_tool_calls_task_id"), "agent_tool_calls", ["task_id"])
    op.create_index(op.f("ix_agent_tool_calls_tool_code"), "agent_tool_calls", ["tool_code"])


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_tool_calls_tool_code"), table_name="agent_tool_calls")
    op.drop_index(op.f("ix_agent_tool_calls_task_id"), table_name="agent_tool_calls")
    op.drop_index(op.f("ix_agent_tool_calls_status"), table_name="agent_tool_calls")
    op.drop_index(op.f("ix_agent_tool_calls_org_id"), table_name="agent_tool_calls")
    op.drop_index(op.f("ix_agent_tool_calls_module"), table_name="agent_tool_calls")
    op.drop_index(op.f("ix_agent_tool_calls_invocation_id"), table_name="agent_tool_calls")
    op.drop_index(op.f("ix_agent_tool_calls_agent_code"), table_name="agent_tool_calls")
    op.drop_table("agent_tool_calls")
