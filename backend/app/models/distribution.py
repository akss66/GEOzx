"""Matrix distribution domain models.

The tables keep the SYNAPSE-style plan -> item execution shape while reusing
BrainTask and AgentToolCall as the durable execution ledger.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin


class MatrixDistributionPlan(Base, TimestampMixin):
    """A multi-account publish preparation plan."""

    __tablename__ = "matrix_distribution_plans"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="SET NULL"), index=True, nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    platforms: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    account_ids: Mapped[list[int]] = mapped_column(JSONVariant, default=list, nullable=False)
    material_ids: Mapped[list[int]] = mapped_column(JSONVariant, default=list, nullable=False)
    topics: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    cover_material_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True, nullable=False)

    items: Mapped[list["MatrixDistributionItem"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class MatrixDistributionItem(Base, TimestampMixin):
    """One account/material publish package inside a matrix plan."""

    __tablename__ = "matrix_distribution_items"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("matrix_distribution_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    material_id: Mapped[int] = mapped_column(
        ForeignKey("material_assets.id", ondelete="CASCADE"), index=True, nullable=False
    )
    platform: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default="waiting_manual", index=True, nullable=False
    )
    tool_call_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_tool_calls.id", ondelete="SET NULL"), nullable=True
    )
    publish_package: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    plan: Mapped[MatrixDistributionPlan] = relationship(back_populates="items")
