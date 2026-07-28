"""Add durable conversation threads and per-message turns.

Revision ID: 20260728_0100
Revises: 20260727_0300
Create Date: 2026-07-28 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260728_0100"
down_revision: str | None = "20260727_0300"
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


def upgrade() -> None:
    op.create_table(
        "conversation_threads",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("created_by_id", BigIntPK, nullable=True),
        sa.Column("client_id", BigIntPK, nullable=True),
        sa.Column("project_id", BigIntPK, nullable=True),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("title", sa.String(length=300), server_default="", nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("id", "org_id", name="uq_conversation_thread_id_org"),
    )
    for column in ("org_id", "created_by_id", "client_id", "project_id", "account_id"):
        op.create_index(
            op.f(f"ix_conversation_threads_{column}"),
            "conversation_threads",
            [column],
        )

    op.create_table(
        "conversation_turns",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("thread_id", BigIntPK, nullable=False),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("created_by_id", BigIntPK, nullable=True),
        sa.Column("client_message_id", sa.String(length=128), nullable=True),
        sa.Column("user_input", sa.Text(), nullable=False),
        sa.Column("assistant_response", sa.Text(), nullable=True),
        sa.Column("intent", JSONVariant, nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["conversation_threads.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id", "org_id"],
            ["conversation_threads.id", "conversation_threads.org_id"],
            name="fk_conversation_turn_thread_org",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "thread_id",
            "client_message_id",
            name="uq_conversation_turn_thread_client_message",
        ),
        sa.UniqueConstraint(
            "id",
            "thread_id",
            "org_id",
            name="uq_conversation_turn_id_thread_org",
        ),
    )
    for column in ("thread_id", "org_id", "created_by_id"):
        op.create_index(
            op.f(f"ix_conversation_turns_{column}"),
            "conversation_turns",
            [column],
        )

    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(sa.Column("thread_id", BigIntPK, nullable=True))
        batch_op.add_column(sa.Column("turn_id", BigIntPK, nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_runs_thread_id_conversation_threads",
            "conversation_threads",
            ["thread_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_agent_runs_turn_id_conversation_turns",
            "conversation_turns",
            ["turn_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_agent_runs_thread_org",
            "conversation_threads",
            ["thread_id", "org_id"],
            ["id", "org_id"],
        )
        batch_op.create_foreign_key(
            "fk_agent_runs_turn_thread_org",
            "conversation_turns",
            ["turn_id", "thread_id", "org_id"],
            ["id", "thread_id", "org_id"],
        )
        batch_op.create_check_constraint(
            "ck_agent_runs_turn_requires_thread",
            "turn_id IS NULL OR thread_id IS NOT NULL",
        )
        batch_op.create_index(op.f("ix_agent_runs_thread_id"), ["thread_id"])
        batch_op.create_index(op.f("ix_agent_runs_turn_id"), ["turn_id"])


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_index(op.f("ix_agent_runs_turn_id"))
        batch_op.drop_index(op.f("ix_agent_runs_thread_id"))
        batch_op.drop_constraint(
            "fk_agent_runs_turn_thread_org",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_agent_runs_turn_id_conversation_turns",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_agent_runs_thread_org",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_agent_runs_thread_id_conversation_threads",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "ck_agent_runs_turn_requires_thread",
            type_="check",
        )
        batch_op.drop_column("turn_id")
        batch_op.drop_column("thread_id")

    op.drop_table("conversation_turns")
    op.drop_table("conversation_threads")
