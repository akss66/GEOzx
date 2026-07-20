"""配置域：per-Agent 模型配置、外部集成凭证配置。"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin


class ModelProvider(Base, TimestampMixin):
    __tablename__ = "model_providers"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_model_provider_org_code"),
        UniqueConstraint("org_id", "id", name="uq_model_provider_org_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    template_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    credential_source: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_last_four: Mapped[str | None] = mapped_column(String(4), nullable=True)
    key_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(32), default="pending", server_default="pending", nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    models: Mapped[list[str] | None] = mapped_column(JSONVariant, nullable=True)
    models_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    org: Mapped["Org"] = relationship()  # noqa: F821
    created_by: Mapped["User"] = relationship(foreign_keys=[created_by_id])  # noqa: F821
    updated_by: Mapped["User"] = relationship(foreign_keys=[updated_by_id])  # noqa: F821


class ModelConfig(Base, TimestampMixin):
    """每个 Agent 的模型绑定：首选 + 兜底 + 参数。v1 默认 DeepSeek。"""

    __tablename__ = "model_configs"
    __table_args__ = (
        UniqueConstraint("org_id", "agent_code", name="uq_model_config_agent"),
        ForeignKeyConstraint(
            ["org_id", "primary_provider_id"],
            ["model_providers.org_id", "model_providers.id"],
            name="fk_model_configs_primary_provider_org",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["org_id", "fallback_provider_id"],
            ["model_providers.org_id", "model_providers.id"],
            name="fk_model_configs_fallback_provider_org",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    agent_code: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_provider_id: Mapped[int | None] = mapped_column(BigIntPK, index=True, nullable=True)
    fallback_provider_id: Mapped[int | None] = mapped_column(BigIntPK, index=True, nullable=True)
    primary_model: Mapped[str] = mapped_column(String(128), default="deepseek-chat", nullable=False)
    fallback_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)

    org: Mapped["Org"] = relationship()  # noqa: F821
    primary_provider: Mapped["ModelProvider | None"] = relationship(
        primaryjoin=lambda: and_(
            ModelConfig.org_id == ModelProvider.org_id,
            ModelConfig.primary_provider_id == ModelProvider.id,
        ),
        foreign_keys=lambda: [ModelConfig.org_id, ModelConfig.primary_provider_id],
        viewonly=True,
    )
    fallback_provider: Mapped["ModelProvider | None"] = relationship(
        primaryjoin=lambda: and_(
            ModelConfig.org_id == ModelProvider.org_id,
            ModelConfig.fallback_provider_id == ModelProvider.id,
        ),
        foreign_keys=lambda: [ModelConfig.org_id, ModelConfig.fallback_provider_id],
        viewonly=True,
    )


class IntegrationConfig(Base, TimestampMixin):
    """外部集成配置：凭证加密存储（后续接入）+ 开关。"""

    __tablename__ = "integration_configs"
    __table_args__ = (UniqueConstraint("org_id", "provider", name="uq_integration_provider"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    credentials: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)

    org: Mapped["Org"] = relationship()  # noqa: F821
