"""共享知识库：爆款库 / 用户画像 / 提示词库 / 话术库（可读可写）。"""

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, JSONVariant, TimestampMixin, pg_enum
from app.models.enums import KnowledgeCategory


class KnowledgeEntry(Base, TimestampMixin):
    """知识条目。结构化内容存于 payload(JSONB)，按 category 区分用途。"""

    __tablename__ = "knowledge_entries"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    category: Mapped[KnowledgeCategory] = mapped_column(
        pg_enum(KnowledgeCategory, "knowledge_category"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    tags: Mapped[list | None] = mapped_column(JSONVariant, nullable=True)

    org: Mapped["Org"] = relationship()  # noqa: F821
