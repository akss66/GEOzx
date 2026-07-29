"""合规域：ComplianceCheck（脚本合规预检记录，关联 Gate3 脚本合规门）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin, pg_enum
from app.models.enums import ComplianceRisk

if TYPE_CHECKING:
    from app.models.content import ContentItem


class ComplianceCheck(Base, TimestampMixin):
    """脚本合规自动预检记录。

    检测维度：敏感词/违禁词、绝对化用语、限流风险词等。给人工质量门提供参考依据，
    不直接放行/打回（强制门仍由人决策）。findings 存命中明细（词/类别/位置）。
    """

    __tablename__ = "compliance_checks"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    deliverable_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL"), nullable=True
    )
    risk: Mapped[ComplianceRisk] = mapped_column(
        pg_enum(ComplianceRisk, "compliance_risk"), index=True, nullable=False
    )
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    findings: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)

    content_item: Mapped[ContentItem] = relationship()  # noqa: F821
