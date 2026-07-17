"""Formal platform integration models."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin


class PlatformIntegration(Base, TimestampMixin):
    """Org-level platform app configuration and capability state."""

    __tablename__ = "platform_integrations"
    __table_args__ = (UniqueConstraint("org_id", "platform", name="uq_platform_integration"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="not_configured", nullable=False)
    client_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    client_secret_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    redirect_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    js_sdk_domain: Mapped[str | None] = mapped_column(String(500), nullable=True)
    auth_status: Mapped[str] = mapped_column(String(32), default="not_configured", nullable=False)
    data_sync_status: Mapped[str] = mapped_column(
        String(32), default="not_configured", nullable=False
    )
    scopes: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    official_docs: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    org: Mapped["Org"] = relationship()  # noqa: F821

    @property
    def client_secret_configured(self) -> bool:
        return bool(self.client_secret_ref)


class PlatformAccountAuth(Base, TimestampMixin):
    """Per-account official authorization and sync state."""

    __tablename__ = "platform_account_auths"
    __table_args__ = (UniqueConstraint("account_id", name="uq_platform_account_auth_account"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    external_open_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    union_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    auth_status: Mapped[str] = mapped_column(String(32), default="unauthorized", nullable=False)
    data_sync_status: Mapped[str] = mapped_column(
        String(32), default="not_configured", nullable=False
    )
    scopes: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    token_secret_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    refresh_secret_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refresh_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_profile: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)

    org: Mapped["Org"] = relationship()  # noqa: F821
    account: Mapped["Account"] = relationship()  # noqa: F821

    @property
    def token_configured(self) -> bool:
        return bool(self.access_token_encrypted or self.token_secret_ref)
