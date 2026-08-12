"""Persistent WeChat article working copies, image slots, and remote draft mappings."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin, pg_enum
from app.models.enums import ArticleImageSlotStatus
from app.schemas.wechat_article import ArticleDocument

if TYPE_CHECKING:
    from app.models.content import ContentItem, Deliverable
    from app.models.identity import Org, User
    from app.models.material import MaterialAsset
    from app.models.workspace import Account


class ArticleWorkingCopy(Base, TimestampMixin):
    """The mutable autosave document for exactly one account-scoped content item."""

    __tablename__ = "article_working_copies"
    __table_args__ = (
        UniqueConstraint("content_item_id", name="uq_article_working_copy_content_item"),
        CheckConstraint("lock_version > 0", name="ck_article_working_copy_lock_version_positive"),
        ForeignKeyConstraint(
            ["content_item_id", "account_id"],
            ["content_items.id", "content_items.account_id"],
            name="fk_article_working_copy_content_account",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    based_on_deliverable_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL"), index=True, nullable=True
    )
    document: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    content_item: Mapped[ContentItem] = relationship(foreign_keys=[content_item_id])  # noqa: F821
    based_on_deliverable: Mapped[Deliverable | None] = relationship()  # noqa: F821
    updated_by: Mapped[User | None] = relationship()  # noqa: F821

    @validates("document")
    def _validate_document(self, _key: str, value: dict) -> dict:
        return ArticleDocument.model_validate(value).model_dump(mode="json")


class ArticleImageSlot(Base, TimestampMixin):
    """A stable visual placement belonging to an article's account-scoped ContentItem."""

    __tablename__ = "article_image_slots"
    __table_args__ = (
        UniqueConstraint("content_item_id", "stable_key", name="uq_article_image_slot_stable_key"),
        CheckConstraint("lock_version > 0", name="ck_article_image_slot_lock_version_positive"),
        ForeignKeyConstraint(
            ["content_item_id", "account_id"],
            ["content_items.id", "content_items.account_id"],
            name="fk_article_image_slot_content_account",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(BigIntPK, nullable=False)
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(300), nullable=False)
    placement_after_block_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    aspect_ratio: Mapped[str] = mapped_column(String(32), nullable=False)
    visual_brief: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_internal: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ArticleImageSlotStatus] = mapped_column(
        pg_enum(ArticleImageSlotStatus, "article_image_slot_status"),
        default=ArticleImageSlotStatus.PLANNED,
        nullable=False,
    )
    selected_material_id: Mapped[int | None] = mapped_column(
        ForeignKey("material_assets.id", ondelete="SET NULL"), index=True, nullable=True
    )
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    content_item: Mapped[ContentItem] = relationship(foreign_keys=[content_item_id])  # noqa: F821
    selected_material: Mapped[MaterialAsset | None] = relationship()  # noqa: F821


class WechatDraftMapping(Base, TimestampMixin):
    """A scoped mapping from one article to a remote WeChat draft media identifier."""

    __tablename__ = "wechat_draft_mappings"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "account_id",
            "content_item_id",
            name="uq_wechat_draft_mapping_scope",
        ),
        ForeignKeyConstraint(
            ["content_item_id", "account_id"],
            ["content_items.id", "content_items.account_id"],
            name="fk_wechat_draft_mapping_content_account",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["account_id", "org_id"],
            ["accounts.id", "accounts.org_id"],
            name="fk_wechat_draft_mapping_account_org",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False
    )
    media_id: Mapped[str] = mapped_column(String(256), nullable=False)
    remote_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_synced_deliverable_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL"), nullable=True
    )

    org: Mapped[Org] = relationship(foreign_keys=[org_id])  # noqa: F821
    account: Mapped[Account] = relationship(foreign_keys=[account_id])  # noqa: F821
    content_item: Mapped[ContentItem] = relationship(foreign_keys=[content_item_id])  # noqa: F821
    last_synced_deliverable: Mapped[Deliverable | None] = relationship()  # noqa: F821
