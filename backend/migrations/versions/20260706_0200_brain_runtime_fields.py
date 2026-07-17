"""brain runtime fields

Revision ID: 20260706_0200
Revises: 20260706_0100
Create Date: 2026-07-06 02:00:00.000000
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260706_0200"
down_revision: str | None = "20260706_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "brain_tasks",
        sa.Column("runtime_mode", sa.String(length=40), server_default="legacy", nullable=False),
    )
    op.add_column("brain_tasks", sa.Column("thread_id", sa.String(length=120), nullable=True))
    op.create_index(op.f("ix_brain_tasks_thread_id"), "brain_tasks", ["thread_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_brain_tasks_thread_id"), table_name="brain_tasks")
    op.drop_column("brain_tasks", "thread_id")
    op.drop_column("brain_tasks", "runtime_mode")
