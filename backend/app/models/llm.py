"""LLM 调用记录：每次模型调用的模型/Token/成本/延迟/状态（成本看板数据源）。"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import BigIntPK


class LLMCall(Base):
    """一次 LLM 调用的审计/计费记录（不可变，仅 created_at）。"""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("orgs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    agent_code: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
