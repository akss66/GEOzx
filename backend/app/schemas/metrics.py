"""复盘看板 schema：指标录入 + 聚合视图。"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MetricSource


class IngestMetricRequest(BaseModel):
    """录入/回流一条指标快照（E8 抖音回流与手动录入共用）。"""

    content_item_id: int | None = None
    account_id: int | None = None
    source: MetricSource = MetricSource.MANUAL
    stat_date: date
    title: str | None = Field(default=None, max_length=300)
    play: int = 0
    exposure: int = 0
    completion_rate: float = 0.0
    like_rate: float = 0.0
    comment_rate: float = 0.0
    share_rate: float = 0.0
    follower_delta: int = 0


class TrendPoint(BaseModel):
    date: str
    play: int
    exposure: int


class EngagementPoint(BaseModel):
    date: str
    completion_rate: float
    like_rate: float


class RankItem(BaseModel):
    title: str
    completion_rate: float


class ReviewOverview(BaseModel):
    """复盘聚合视图：趋势 + 完播互动 + 内容排名 + 汇总。"""

    model_config = ConfigDict(from_attributes=True)

    has_data: bool
    trend: list[TrendPoint]
    engagement: list[EngagementPoint]
    rank_top: list[RankItem]
    rank_bottom: list[RankItem]
    total_play: int
    avg_completion_rate: float
    follower_delta: int
