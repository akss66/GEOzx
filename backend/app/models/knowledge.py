"""Client-scoped knowledge, agent suggestions, and usage citations."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin, pg_enum
from app.models.enums import KnowledgeCategory

if TYPE_CHECKING:
    from app.models.client import Client, Project
    from app.models.identity import Org


class KnowledgeEntry(Base, TimestampMixin):
    """A reviewed knowledge document available to agents in one client scope."""

    __tablename__ = "knowledge_entries"
    __table_args__ = (
        CheckConstraint(
            "entry_kind IN ('document', 'product_fact', 'policy', 'case', 'brand_voice', "
            "'asset_reference')",
            name="ck_knowledge_entries_entry_kind",
        ),
        CheckConstraint(
            "verification_status IN ('draft', 'verified', 'rejected', 'expired')",
            name="ck_knowledge_entries_verification_status",
        ),
        CheckConstraint(
            "effective_at IS NULL OR expires_at IS NULL OR effective_at <= expires_at",
            name="ck_knowledge_entries_validity_range",
        ),
    )

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
    knowledge_base_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="SET NULL"), index=True, nullable=True
    )
    entry_kind: Mapped[str] = mapped_column(
        String(40), default="document", index=True, nullable=False
    )
    verification_status: Mapped[str] = mapped_column(
        String(40), default="draft", index=True, nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    source_attachment_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_attachments.id", ondelete="SET NULL"), nullable=True
    )
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    allowed_for_external_claim: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    org: Mapped[Org] = relationship()  # noqa: F821
    client: Mapped[Client] = relationship()  # noqa: F821
    project: Mapped[Project | None] = relationship()  # noqa: F821
    citations: Mapped[list[KnowledgeCitation]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )


class KnowledgeBase(Base, TimestampMixin):
    """An organization-owned knowledge scope for one brand or shared policy."""

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'brand' AND client_id IS NOT NULL) OR "
            "(kind = 'organization_shared' AND client_id IS NULL)",
            name="ck_knowledge_bases_kind_client_scope",
        ),
        CheckConstraint(
            "kind IN ('brand', 'organization_shared')",
            name="ck_knowledge_bases_kind",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )


class AccountKnowledgeBinding(Base, TimestampMixin):
    """Explicit account attachment for a reusable organization knowledge base."""

    __tablename__ = "account_knowledge_bindings"
    __table_args__ = (
        CheckConstraint(
            "binding_type IN ('primary_brand', 'shared')",
            name="ck_account_knowledge_bindings_type",
        ),
        Index(
            "uq_account_knowledge_bindings_active_primary_brand",
            "account_id",
            unique=True,
            postgresql_where=text("binding_type = 'primary_brand' AND status = 'active'"),
            sqlite_where=text("binding_type = 'primary_brand' AND status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    knowledge_base_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True, nullable=False
    )
    binding_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True, nullable=False)
    bound_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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
