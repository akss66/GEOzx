"""Extend the external-write ledger for WeChat draft synchronization.

Revision ID: 20260811_0400
Revises: 20260811_0330
Create Date: 2026-08-12 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.base import BigIntPK

revision: str = "20260811_0400"
down_revision: str | None = "20260811_0330"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WECHAT_STATUSES = (
    "wechat_queued",
    "wechat_running",
    "wechat_synced",
    "wechat_conflict",
    "wechat_blocked",
    "wechat_reconciliation_required",
)


def _operation_type() -> sa.types.TypeEngine:
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.ENUM(
            "legacy_douyin_publish",
            "draft_sync",
            name="platform_publish_job_operation_type",
            create_type=False,
        )
    return sa.String(length=40)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(
            "legacy_douyin_publish",
            "draft_sync",
            name="platform_publish_job_operation_type",
        ).create(bind, checkfirst=True)
        for status in _WECHAT_STATUSES:
            op.execute(
                sa.text(
                    "ALTER TYPE platform_publish_job_status ADD VALUE IF NOT EXISTS "
                    f"'{status}'"
                )
            )

    op.add_column(
        "platform_publish_jobs",
        sa.Column(
            "operation_type",
            _operation_type(),
            server_default="legacy_douyin_publish",
            nullable=False,
        ),
    )
    op.add_column(
        "platform_publish_jobs",
        sa.Column("external_media_id", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "platform_publish_jobs",
        sa.Column("article_version_id", BigIntPK, nullable=True),
    )
    op.add_column(
        "platform_publish_jobs",
        sa.Column("expected_remote_hash", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "platform_publish_jobs",
        sa.Column("observed_remote_hash", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "platform_publish_jobs",
        sa.Column("request_digest", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_platform_publish_jobs_article_version",
        "platform_publish_jobs",
        "deliverables",
        ["article_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for column in ("operation_type", "external_media_id", "article_version_id"):
        op.create_index(
            op.f(f"ix_platform_publish_jobs_{column}"),
            "platform_publish_jobs",
            [column],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        active = bind.execute(
            sa.text(
                "SELECT 1 FROM platform_publish_jobs "
                "WHERE operation_type = 'draft_sync' "
                "OR status::text LIKE 'wechat_%' LIMIT 1"
            )
        ).first()
        if active is not None:
            raise RuntimeError(
                "cannot downgrade while WeChat draft-sync jobs use additive enum values"
            )
    else:
        active = bind.execute(
            sa.text(
                "SELECT 1 FROM platform_publish_jobs "
                "WHERE operation_type = 'draft_sync' OR status LIKE 'wechat_%' LIMIT 1"
            )
        ).first()
        if active is not None:
            raise RuntimeError("cannot downgrade while WeChat draft-sync jobs exist")

    for column in ("article_version_id", "external_media_id", "operation_type"):
        op.drop_index(op.f(f"ix_platform_publish_jobs_{column}"), table_name="platform_publish_jobs")
    op.drop_constraint(
        "fk_platform_publish_jobs_article_version",
        "platform_publish_jobs",
        type_="foreignkey",
    )
    for column in (
        "request_digest",
        "observed_remote_hash",
        "expected_remote_hash",
        "article_version_id",
        "external_media_id",
        "operation_type",
    ):
        op.drop_column("platform_publish_jobs", column)
    if bind.dialect.name == "postgresql":
        postgresql.ENUM(
            "legacy_douyin_publish",
            "draft_sync",
            name="platform_publish_job_operation_type",
        ).drop(bind, checkfirst=True)
    # PostgreSQL cannot remove enum labels transactionally. The additive
    # platform_publish_job_status values intentionally remain installed.
