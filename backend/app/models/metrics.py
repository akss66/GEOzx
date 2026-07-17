"""数据指标域：MetricSnapshot（各平台内容数据快照，复盘看板的数据源）。"""

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import BigIntPK, TimestampMixin, pg_enum
from app.models.enums import MetricSource


class MetricSnapshot(Base, TimestampMixin):
    """一条内容在某账号某天的指标快照（E8 抖音回流写入，复盘看板聚合读取）。

    指标字段对齐 SPEC 5.6：播放/曝光/完播率/点赞率/评论率/转发率/涨粉。
    比率字段以小数存（如 0.34 = 34%）。同 (content_item, date) 视为一条，重复回流则更新。
    """

    __tablename__ = "metric_snapshots"

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

    org: Mapped["Org"] = relationship()  # noqa: F821


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
