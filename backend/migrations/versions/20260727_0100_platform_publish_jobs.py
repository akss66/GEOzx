"""Add the official platform publishing job ledger.

Revision ID: 20260727_0100
Revises: 20260723_0200
Create Date: 2026-07-27 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.base import BigIntPK, JSONVariant

revision: str = "20260727_0100"
down_revision: str | None = "20260723_0200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

publish_job_status_enum = postgresql.ENUM(
    "draft",
    "pending_approval",
    "task_created",
    "handoff_ready",
    "user_publishing",
    "waiting_bind",
    "bound",
    "observing",
    "completed",
    "failed",
    "expired",
    "cancelled",
    name="platform_publish_job_status",
    create_type=False,
)
platform_enum = postgresql.ENUM(
    "douyin",
    "xiaohongshu",
    "shipinhao",
    name="platform",
    create_type=False,
)


def _status_type() -> sa.types.TypeEngine:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return publish_job_status_enum
    return sa.String(length=40)


def _platform_type() -> sa.types.TypeEngine:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return platform_enum
    return sa.String(length=40)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        publish_job_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "platform_publish_jobs",
        sa.Column("id", BigIntPK, primary_key=True),
        sa.Column("org_id", BigIntPK, nullable=False),
        sa.Column("account_id", BigIntPK, nullable=False),
        sa.Column("active_client_id", BigIntPK, nullable=True),
        sa.Column("active_project_id", BigIntPK, nullable=True),
        sa.Column("created_by_id", BigIntPK, nullable=True),
        sa.Column("brain_task_id", BigIntPK, nullable=True),
        sa.Column("tool_call_id", BigIntPK, nullable=True),
        sa.Column("platform_content_record_id", BigIntPK, nullable=True),
        sa.Column("platform", _platform_type(), nullable=False),
        sa.Column(
            "status",
            _status_type(),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column(
            "publish_package",
            JSONVariant,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "capabilities_snapshot",
            JSONVariant,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "approval_snapshot",
            JSONVariant,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("share_id", sa.String(length=160), nullable=True),
        sa.Column("posting_task_id", sa.String(length=160), nullable=True),
        sa.Column("external_video_id", sa.String(length=160), nullable=True),
        sa.Column("external_item_id", sa.String(length=160), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handoff_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("last_platform_log_id", sa.String(length=160), nullable=True),
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
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["active_client_id"], ["clients.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["active_project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["brain_task_id"], ["brain_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["tool_call_id"], ["agent_tool_calls.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["platform_content_record_id"],
            ["platform_content_records.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "org_id",
            "idempotency_key",
            name="uq_platform_publish_jobs_org_idempotency",
        ),
        sa.UniqueConstraint(
            "org_id",
            "platform",
            "share_id",
            name="uq_platform_publish_jobs_org_platform_share",
        ),
    )
    for column in (
        "org_id",
        "account_id",
        "active_client_id",
        "active_project_id",
        "created_by_id",
        "brain_task_id",
        "tool_call_id",
        "platform_content_record_id",
        "platform",
        "status",
        "share_id",
        "posting_task_id",
        "external_video_id",
        "external_item_id",
    ):
        op.create_index(
            op.f(f"ix_platform_publish_jobs_{column}"),
            "platform_publish_jobs",
            [column],
        )
    op.create_index(
        "ix_platform_publish_jobs_org_account_status",
        "platform_publish_jobs",
        ["org_id", "account_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_publish_jobs_org_account_status",
        table_name="platform_publish_jobs",
    )
    for column in reversed(
        (
            "org_id",
            "account_id",
            "active_client_id",
            "active_project_id",
            "created_by_id",
            "brain_task_id",
            "tool_call_id",
            "platform_content_record_id",
            "platform",
            "status",
            "share_id",
            "posting_task_id",
            "external_video_id",
            "external_item_id",
        )
    ):
        op.drop_index(
            op.f(f"ix_platform_publish_jobs_{column}"),
            table_name="platform_publish_jobs",
        )
    op.drop_table("platform_publish_jobs")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        publish_job_status_enum.drop(bind, checkfirst=True)
