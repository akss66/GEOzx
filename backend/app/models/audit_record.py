"""Irreversible, content-free audit facts retained after private data deletion."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import BigIntPK, JSONVariant


class AuditRecord(Base):
    """Minimal business audit fact with no conversation or runtime identifiers."""

    __tablename__ = "audit_records"
    __table_args__ = (
        CheckConstraint(
            "category IN ('approval', 'publish', 'cost')",
            name="ck_audit_records_category",
        ),
        Index(
            "ix_audit_records_org_account_category_occurred",
            "org_id",
            "account_id",
            "category",
            "occurred_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    amount_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    details: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
