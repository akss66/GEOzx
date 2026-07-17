"""Add client-scoped knowledge suggestions and citation ledger.

Revision ID: 20260717_0200
Revises: 20260717_0100
Create Date: 2026-07-17 04:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260717_0200"
down_revision: str | None = "20260717_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

knowledge_category = postgresql.ENUM(
    "hot_content",
    "user_persona",
    "prompt_library",
    "script_library",
    name="knowledge_category",
    create_type=False,
)


def upgrade() -> None:
    op.add_column("knowledge_entries", sa.Column("client_id", BigIntPK, nullable=True))
    op.add_column("knowledge_entries", sa.Column("project_id", BigIntPK, nullable=True))
    op.add_column(
        "knowledge_entries",
        sa.Column("content", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "knowledge_entries",
        sa.Column("source_type", sa.String(length=40), server_default="manual", nullable=False),
    )
    op.add_column(
        "knowledge_entries",
        sa.Column(
            "source_label",
            sa.String(length=300),
            server_default="历史知识迁移",
            nullable=False,
        ),
    )
    op.add_column("knowledge_entries", sa.Column("source_url", sa.String(length=1000)))
    op.add_column(
        "knowledge_entries",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "knowledge_entries",
        sa.Column("status", sa.String(length=40), server_default="active", nullable=False),
    )
    op.add_column("knowledge_entries", sa.Column("created_by_id", BigIntPK, nullable=True))
    op.create_foreign_key(
        "fk_knowledge_entries_client_id",
        "knowledge_entries",
        "clients",
        ["client_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_knowledge_entries_project_id",
        "knowledge_entries",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_knowledge_entries_created_by_id",
        "knowledge_entries",
        "users",
        ["created_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE knowledge_entries AS knowledge
        SET client_id = (
            SELECT client.id FROM clients AS client
            WHERE client.org_id = knowledge.org_id
            ORDER BY client.id LIMIT 1
        ),
        content = COALESCE(knowledge.payload ->> 'note', ''),
        source_label = '历史知识迁移'
        """
    )
    op.alter_column("knowledge_entries", "client_id", nullable=False)
    op.create_index(op.f("ix_knowledge_entries_client_id"), "knowledge_entries", ["client_id"])
    op.create_index(op.f("ix_knowledge_entries_project_id"), "knowledge_entries", ["project_id"])
    op.create_index(op.f("ix_knowledge_entries_status"), "knowledge_entries", ["status"])

    op.create_table(
        "knowledge_suggestions",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("client_id", BigIntPK, nullable=False),
        sa.Column("project_id", BigIntPK, nullable=True),
        sa.Column("category", knowledge_category, nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload", JSONVariant, nullable=False),
        sa.Column("tags", JSONVariant, nullable=True),
        sa.Column("source_agent_code", sa.String(length=64), nullable=False),
        sa.Column("source_label", sa.String(length=300), nullable=False),
        sa.Column("source_task_id", BigIntPK, nullable=True),
        sa.Column("source_deliverable_id", BigIntPK, nullable=True),
        sa.Column("status", sa.String(length=40), server_default="pending", nullable=False),
        sa.Column("reviewed_by_id", BigIntPK, nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("accepted_entry_id", BigIntPK, nullable=True),
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
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_task_id"], ["brain_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_deliverable_id"], ["deliverables.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["accepted_entry_id"], ["knowledge_entries.id"], ondelete="SET NULL"
        ),
    )
    for column in ("org_id", "client_id", "project_id", "category", "status"):
        op.create_index(
            op.f(f"ix_knowledge_suggestions_{column}"),
            "knowledge_suggestions",
            [column],
        )

    op.create_table(
        "knowledge_citations",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("client_id", BigIntPK, nullable=False),
        sa.Column("project_id", BigIntPK, nullable=True),
        sa.Column("entry_id", BigIntPK, nullable=False),
        sa.Column("task_id", BigIntPK, nullable=True),
        sa.Column("invocation_id", BigIntPK, nullable=True),
        sa.Column("agent_code", sa.String(length=64), nullable=False),
        sa.Column("context", sa.String(length=500), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["orgs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entry_id"], ["knowledge_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["brain_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["invocation_id"], ["agent_invocations.id"], ondelete="SET NULL"),
    )
    for column in ("org_id", "client_id", "project_id", "entry_id", "task_id", "invocation_id"):
        op.create_index(op.f(f"ix_knowledge_citations_{column}"), "knowledge_citations", [column])


def downgrade() -> None:
    op.drop_table("knowledge_citations")
    op.drop_table("knowledge_suggestions")
    for column in ("status", "project_id", "client_id"):
        op.drop_index(op.f(f"ix_knowledge_entries_{column}"), table_name="knowledge_entries")
    for constraint in (
        "fk_knowledge_entries_created_by_id",
        "fk_knowledge_entries_project_id",
        "fk_knowledge_entries_client_id",
    ):
        op.drop_constraint(constraint, "knowledge_entries", type_="foreignkey")
    for column in (
        "created_by_id",
        "status",
        "version",
        "source_url",
        "source_label",
        "source_type",
        "content",
        "project_id",
        "client_id",
    ):
        op.drop_column("knowledge_entries", column)
