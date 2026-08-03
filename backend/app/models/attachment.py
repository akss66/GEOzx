"""Account-scoped files attached to one owned conversation."""

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin


class ConversationAttachment(Base, TimestampMixin):
    __tablename__ = "conversation_attachments"
    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_conversation_attachments_size_positive"),
        CheckConstraint(
            "scan_status IN ('pending', 'clean', 'rejected')",
            name="ck_conversation_attachments_scan_status",
        ),
        CheckConstraint(
            "parse_status IN ('pending', 'ready', 'failed')",
            name="ck_conversation_attachments_parse_status",
        ),
        UniqueConstraint("storage_key", name="uq_conversation_attachments_storage_key"),
        Index(
            "ix_conversation_attachments_owner_scope",
            "org_id",
            "created_by_id",
            "account_id",
            "thread_id",
        ),
        Index("ix_conversation_attachments_sha256", "sha256"),
        Index("ix_conversation_attachments_thread_created", "thread_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    parse_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    parsed_context: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
