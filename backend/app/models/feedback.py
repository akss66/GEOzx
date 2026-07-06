"""闭环反馈域：运营复盘建议的采纳与验证追踪。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, TimestampMixin, pg_enum
from app.models.enums import OptimizationSuggestionStatus


class OptimizationSuggestion(Base, TimestampMixin):
    """运营复盘产生的优化建议，用于驱动下一轮内容生产改进。"""

    __tablename__ = "optimization_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "source_deliverable_id",
            "suggestion",
            name="uq_optimization_suggestion_source_text",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_deliverable_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL"), index=True, nullable=True
    )
    target_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[OptimizationSuggestionStatus] = mapped_column(
        pg_enum(OptimizationSuggestionStatus, "optimization_suggestion_status"),
        default=OptimizationSuggestionStatus.SUGGESTED,
        index=True,
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    org: Mapped["Org"] = relationship()  # noqa: F821
    content_item: Mapped["ContentItem"] = relationship()  # noqa: F821
    source_deliverable: Mapped["Deliverable"] = relationship()  # noqa: F821
