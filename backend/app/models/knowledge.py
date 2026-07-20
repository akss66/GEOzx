"""Client-scoped knowledge, agent suggestions, and usage citations."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin, pg_enum
from app.models.enums import KnowledgeCategory


class KnowledgeEntry(Base, TimestampMixin):
    """A reviewed knowledge document available to agents in one client scope."""

    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    category: Mapped[KnowledgeCategory] = mapped_column(
        pg_enum(KnowledgeCategory, "knowledge_category"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    tags: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)
    source_type: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    source_label: Mapped[str] = mapped_column(String(300), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )

    org: Mapped["Org"] = relationship()  # noqa: F821
    client: Mapped["Client"] = relationship()  # noqa: F821
    project: Mapped["Project | None"] = relationship()  # noqa: F821
    citations: Mapped[list["KnowledgeCitation"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )


class KnowledgeSuggestion(Base, TimestampMixin):
    """An agent-proposed item that cannot enter the library without review."""

    __tablename__ = "knowledge_suggestions"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    category: Mapped[KnowledgeCategory] = mapped_column(
        pg_enum(KnowledgeCategory, "knowledge_category"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, default=dict, nullable=False)
    tags: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)
    source_agent_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_label: Mapped[str] = mapped_column(String(300), nullable=False)
    source_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="SET NULL"), index=True, nullable=True
    )
    source_deliverable_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL"), index=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True, nullable=False)
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_entries.id", ondelete="SET NULL"), nullable=True
    )


class KnowledgeCitation(Base):
    """An immutable record of an agent/task using a knowledge entry."""

    __tablename__ = "knowledge_citations"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=False
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=True
    )
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_entries.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("brain_tasks.id", ondelete="SET NULL"), index=True, nullable=True
    )
    invocation_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_invocations.id", ondelete="SET NULL"), index=True, nullable=True
    )
    agent_code: Mapped[str] = mapped_column(String(64), nullable=False)
    context: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entry: Mapped[KnowledgeEntry] = relationship(back_populates="citations")
