"""Add durable deliverable actions and operator resources.

Revision ID: 20260804_0200
Revises: 20260804_0100
Create Date: 2026-08-04 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260804_0200"
down_revision: str | None = "20260804_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deliverable_action_executions",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "account_id", BigIntPK, sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "requested_by_id",
            BigIntPK,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            BigIntPK,
            sa.ForeignKey("deliverables.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("artifact_version", sa.Integer(), nullable=False),
        sa.Column("action_code", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "confirmed_by_id",
            BigIntPK,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resource_type", sa.String(64), nullable=True),
        sa.Column("resource_id", BigIntPK, nullable=True),
        sa.Column("result_payload", JSONVariant, nullable=False),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "org_id", "requested_by_id", "idempotency_key", name="uq_deliverable_action_idempotency"
        ),
    )
    op.create_index(
        "ix_deliverable_action_executions_org_id", "deliverable_action_executions", ["org_id"]
    )
    op.create_index(
        "ix_deliverable_action_executions_account_id",
        "deliverable_action_executions",
        ["account_id"],
    )
    op.create_index(
        "ix_deliverable_action_executions_artifact_id",
        "deliverable_action_executions",
        ["artifact_id"],
    )

    op.create_table(
        "shoot_tasks",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "account_id", BigIntPK, sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "content_item_id",
            BigIntPK,
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_artifact_id",
            BigIntPK,
            sa.ForeignKey("deliverables.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_artifact_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_by_id",
            BigIntPK,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assignee_id", BigIntPK, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for name in ("org_id", "account_id", "content_item_id", "source_artifact_id"):
        op.create_index(f"ix_shoot_tasks_{name}", "shoot_tasks", [name])

    op.create_table(
        "content_schedule_entries",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "account_id", BigIntPK, sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "content_item_id",
            BigIntPK,
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_artifact_id",
            BigIntPK,
            sa.ForeignKey("deliverables.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_artifact_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_by_id",
            BigIntPK,
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for name in ("org_id", "account_id", "content_item_id", "source_artifact_id"):
        op.create_index(f"ix_content_schedule_entries_{name}", "content_schedule_entries", [name])


def downgrade() -> None:
    op.drop_table("content_schedule_entries")
    op.drop_table("shoot_tasks")
    op.drop_table("deliverable_action_executions")
