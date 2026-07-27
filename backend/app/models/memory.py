"""Runtime working-memory projections for long-lived agent threads."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin


class RuntimeMemory(Base, TimestampMixin):
    """Compact, auditable projection of a BrainTask runtime thread."""

    __tablename__ = "runtime_memories"
    __table_args__ = (
        UniqueConstraint("org_id", "task_id", name="uq_runtime_memory_org_task"),
        UniqueConstraint("org_id", "thread_id", name="uq_runtime_memory_org_thread"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    thread_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    last_event_id: Mapped[int | None] = mapped_column(BigIntPK, nullable=True)
    source_event_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    prompt_id: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    prompt_schema_version: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    compacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
