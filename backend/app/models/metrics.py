"""数据指标域：MetricSnapshot（各平台内容数据快照，复盘看板的数据源）。"""

from datetime import date

from sqlalchemy import (
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    and_,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.account_data import DataImportBatch, PlatformContentRecord
from app.models.base import BigIntPK, TimestampMixin, pg_enum
from app.models.enums import MetricSource


class MetricSnapshot(Base, TimestampMixin):
    """一条内容在某账号某天的指标快照（E8 抖音回流写入，复盘看板聚合读取）。

    指标字段对齐 SPEC 5.6：播放/曝光/完播率/点赞率/评论率/转发率/涨粉。
    比率字段以小数存（如 0.34 = 34%）。同 (content_item, date) 视为一条，重复回流则更新。
    """

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "import_batch_id",
            "platform_content_record_id",
            "stat_date",
            name="uq_metric_snapshots_import_projection",
        ),
        CheckConstraint(
            "(import_batch_id IS NULL AND platform_content_record_id IS NULL) "
            "OR account_id IS NOT NULL",
            name="ck_metric_snapshots_account_required_for_source_links",
        ),
        ForeignKeyConstraint(
            ["org_id", "account_id", "import_batch_id"],
            [
                "data_import_batches.org_id",
                "data_import_batches.account_id",
                "data_import_batches.id",
            ],
            name="fk_metric_snapshots_import_batch_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "account_id", "platform_content_record_id"],
            [
                "platform_content_records.org_id",
                "platform_content_records.account_id",
                "platform_content_records.id",
            ],
            name="fk_metric_snapshots_content_scope",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    content_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True, nullable=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), index=True, nullable=True
    )
    import_batch_id: Mapped[int | None] = mapped_column(BigIntPK, index=True, nullable=True)
    platform_content_record_id: Mapped[int | None] = mapped_column(
        BigIntPK, index=True, nullable=True
    )
    source: Mapped[MetricSource] = mapped_column(
        pg_enum(MetricSource, "metric_source"), nullable=False
    )
    stat_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)

    play: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    exposure: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    like_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    comment_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    share_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    follower_delta: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    like_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    share_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    favorite_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cover_click_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_watch_time_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    org: Mapped["Org"] = relationship()  # noqa: F821
    import_batch: Mapped["DataImportBatch | None"] = relationship(  # noqa: F821
        primaryjoin=lambda: and_(
            MetricSnapshot.org_id == DataImportBatch.org_id,  # noqa: F821
            MetricSnapshot.account_id == DataImportBatch.account_id,  # noqa: F821
            MetricSnapshot.import_batch_id == DataImportBatch.id,  # noqa: F821
        ),
        foreign_keys=lambda: [
            MetricSnapshot.org_id,
            MetricSnapshot.account_id,
            MetricSnapshot.import_batch_id,
        ],
        viewonly=True,
    )
    platform_content_record: Mapped["PlatformContentRecord | None"] = relationship(  # noqa: F821
        primaryjoin=lambda: and_(
            MetricSnapshot.org_id == PlatformContentRecord.org_id,  # noqa: F821
            MetricSnapshot.account_id == PlatformContentRecord.account_id,  # noqa: F821
            MetricSnapshot.platform_content_record_id == PlatformContentRecord.id,  # noqa: F821
        ),
        foreign_keys=lambda: [
            MetricSnapshot.org_id,
            MetricSnapshot.account_id,
            MetricSnapshot.platform_content_record_id,
        ],
        viewonly=True,
    )


class AccountReviewGoal(Base, TimestampMixin):
    """Rolling performance target for one account and review horizon."""

    __tablename__ = "account_review_goals"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "period_days",
            name="uq_account_review_goals_account_period",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("orgs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    period_days: Mapped[int] = mapped_column(Integer, nullable=False)
    target_play: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_completion_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_follower_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
