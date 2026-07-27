"""Add runtime working memory projections.

Revision ID: 20260721_0400
Revises: 20260721_0300
Create Date: 2026-07-21 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260721_0400"
down_revision: str | None = "20260721_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_memories",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("task_id", BigIntPK, nullable=False),
        sa.Column("thread_id", sa.String(length=120), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("snapshot", JSONVariant, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("last_event_id", BigIntPK, nullable=True),
        sa.Column("source_event_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("prompt_id", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("prompt_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column(
            "prompt_schema_version",
            sa.String(length=80),
            nullable=False,
            server_default="",
        ),
        sa.Column("compacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["brain_tasks.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("org_id", "task_id", name="uq_runtime_memory_org_task"),
        sa.UniqueConstraint("org_id", "thread_id", name="uq_runtime_memory_org_thread"),
    )
    for column in ("org_id", "task_id", "thread_id"):
        op.create_index(op.f(f"ix_runtime_memories_{column}"), "runtime_memories", [column])


def downgrade() -> None:
    for column in ("thread_id", "task_id", "org_id"):
        op.drop_index(op.f(f"ix_runtime_memories_{column}"), table_name="runtime_memories")
    op.drop_table("runtime_memories")
