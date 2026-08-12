"""Durable ledger for official platform publishing handoffs and callbacks."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin, pg_enum
from app.models.enums import Platform


class PlatformPublishJobStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    TASK_CREATED = "task_created"
    HANDOFF_READY = "handoff_ready"
    USER_PUBLISHING = "user_publishing"
    WAITING_BIND = "waiting_bind"
    BOUND = "bound"
    OBSERVING = "observing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    WECHAT_QUEUED = "wechat_queued"
    WECHAT_RUNNING = "wechat_running"
    WECHAT_SYNCED = "wechat_synced"
    WECHAT_CONFLICT = "wechat_conflict"
    WECHAT_BLOCKED = "wechat_blocked"
    WECHAT_RECONCILIATION_REQUIRED = "wechat_reconciliation_required"


class PlatformPublishJobOperationType(StrEnum):
    """Stable operation discriminator for the shared external-write ledger."""

    LEGACY_DOUYIN_PUBLISH = "legacy_douyin_publish"
    WECHAT_DRAFT_SYNC = "draft_sync"


class PlatformPublishJob(Base, TimestampMixin):
    """One approved package moving through an official platform publish flow.

    The row freezes the account and optional workspace context used for the
    external write. Ephemeral signed schema URLs are intentionally not stored.
    """

    __tablename__ = "platform_publish_jobs"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "idempotency_key",
            name="uq_platform_publish_jobs_org_idempotency",
        ),
        UniqueConstraint(
            "org_id",
            "platform",
            "share_id",
            name="uq_platform_publish_jobs_org_platform_share",
        ),
        Index(
            "ix_platform_publish_jobs_org_account_status",
            "org_id",
            "account_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    active_client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), index=True, nullable=True
    )
    active_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True, nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    brain_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="SET NULL"), index=True, nullable=True
    )
    tool_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_tool_calls.id", ondelete="SET NULL"), index=True, nullable=True
    )
    platform_content_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_content_records.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    platform: Mapped[Platform] = mapped_column(
        pg_enum(Platform, "platform"), index=True, nullable=False
    )
    status: Mapped[PlatformPublishJobStatus] = mapped_column(
        pg_enum(PlatformPublishJobStatus, "platform_publish_job_status"),
        default=PlatformPublishJobStatus.DRAFT,
        index=True,
        nullable=False,
    )
    operation_type: Mapped[PlatformPublishJobOperationType] = mapped_column(
        pg_enum(PlatformPublishJobOperationType, "platform_publish_job_operation_type"),
        default=PlatformPublishJobOperationType.LEGACY_DOUYIN_PUBLISH,
        index=True,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    publish_package: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    capabilities_snapshot: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    approval_snapshot: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)

    share_id: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    posting_task_id: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    external_video_id: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    external_item_id: Mapped[str | None] = mapped_column(String(160), index=True, nullable=True)
    external_media_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    article_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    expected_remote_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observed_remote_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    handoff_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_platform_log_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
