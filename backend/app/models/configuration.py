"""配置域：per-Agent 模型配置、外部集成凭证配置。"""

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin


class ModelConfig(Base, TimestampMixin):
    """每个 Agent 的模型绑定：首选 + 兜底 + 参数。v1 默认 DeepSeek。"""

    __tablename__ = "model_configs"
    __table_args__ = (UniqueConstraint("org_id", "agent_code", name="uq_model_config_agent"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    agent_code: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_model: Mapped[str] = mapped_column(String(128), default="deepseek-chat", nullable=False)
    fallback_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)

    org: Mapped["Org"] = relationship()  # noqa: F821


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
