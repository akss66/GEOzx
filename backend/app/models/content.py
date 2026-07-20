"""内容域：ContentItem（贯穿全链路的内容实例）、Deliverable（多态交付物，版本化）。"""

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin, pg_enum
from app.models.enums import (
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
)


class ContentItem(Base, TimestampMixin):
    """一条内容贯穿全链路的实例（含当前阶段、状态）。"""

    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    current_stage: Mapped[ContentStage] = mapped_column(
        pg_enum(ContentStage, "content_stage"),
        default=ContentStage.POSITIONING,
        nullable=False,
    )
    status: Mapped[ContentStatus] = mapped_column(
        pg_enum(ContentStatus, "content_status"),
        default=ContentStatus.DRAFT,
        nullable=False,
    )

    project: Mapped["Project"] = relationship(back_populates="content_items")  # noqa: F821
    deliverables: Mapped[list["Deliverable"]] = relationship(
        back_populates="content_item", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["AgentTask"]] = relationship(  # noqa: F821
        back_populates="content_item", cascade="all, delete-orphan"
    )


class Deliverable(Base, TimestampMixin):
    """交付物：type + version + agent + status + payload(JSONB)。

    多态：payload 结构按 `type` 用对应 Pydantic schema 校验（见 app/schemas/deliverable.py）。
    同一内容同一 type 的多个版本通过 (content_item_id, type, version) 唯一约束区分。
    """

    __tablename__ = "deliverables"
    __table_args__ = (
        UniqueConstraint("content_item_id", "type", "version", name="uq_deliverable_version"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    agent_code: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[DeliverableType] = mapped_column(
        pg_enum(DeliverableType, "deliverable_type"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[DeliverableStatus] = mapped_column(
        pg_enum(DeliverableStatus, "deliverable_status"),
        default=DeliverableStatus.DRAFT,
        nullable=False,
    )
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    content_item: Mapped["ContentItem"] = relationship(back_populates="deliverables")
