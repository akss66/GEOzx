"""素材域：MaterialAsset（生成/上传的视频等素材，落本地卷归档）。"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, TimestampMixin, pg_enum
from app.models.enums import MaterialStatus


class MaterialAsset(Base, TimestampMixin):
    """素材资产：AI 生成的视频等。

    生成任务异步执行（worker generate_video）：提交→轮询→下载落本地卷（storage_local_dir）。
    `local_path` 为卷内相对路径；`source_url` 为供应商临时 URL（可能过期，仅留痕）。
    """

    __tablename__ = "material_assets"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=True
    )
    deliverable_id: Mapped[int | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL"), index=True, nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="video", nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[MaterialStatus] = mapped_column(
        pg_enum(MaterialStatus, "material_status"),
        default=MaterialStatus.QUEUED,
        index=True,
        nullable=False,
    )
    external_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    local_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    org: Mapped["Org"] = relationship()  # noqa: F821
