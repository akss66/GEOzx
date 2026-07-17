"""复盘看板 schema：指标录入 + 聚合视图。"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import MetricSource
from app.schemas.feedback import OptimizationSuggestionOut


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


class PerformanceSnapshotOut(BaseModel):
    """A normalized content performance snapshot for review loops."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    content_item_id: int | None
    account_id: int | None
    source: MetricSource
    stat_date: date
    title: str | None
    play: int
    exposure: int
    completion_rate: float
    like_rate: float
    comment_rate: float
    share_rate: float
    follower_delta: int
    created_at: datetime


class ReviewGoalUpsert(BaseModel):
    period_days: Literal[7, 30, 90]
    target_play: int | None = Field(default=None, ge=1)
    target_completion_rate: float | None = Field(default=None, gt=0, le=1)
    target_follower_delta: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_target(self):
        if not any(
            value is not None
            for value in (
                self.target_play,
                self.target_completion_rate,
                self.target_follower_delta,
            )
        ):
            raise ValueError("至少设置一项周期目标")
        return self


class AccountReviewGoalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    period_days: int
    target_play: int | None = None
    target_completion_rate: float | None = None
    target_follower_delta: int | None = None
    status: Literal["not_configured", "insufficient_data", "behind", "on_track", "achieved"]
    achievement_percent: float | None = None
    components: list[dict] = Field(default_factory=list)
    summary: str


class ReviewAccountOut(BaseModel):
    id: int
    nickname: str
    platform: str
    auth_status: str
    data_sync_status: str


class ReviewPeriodOut(BaseModel):
    days: int
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date


class ReviewDataStatusOut(BaseModel):
    has_data: bool
    sources: list[MetricSource]
    latest_stat_date: date | None = None
    latest_synced_at: datetime | None = None
    missing_reasons: list[str] = Field(default_factory=list)


class ReviewTotalsOut(BaseModel):
    play: int = 0
    exposure: int = 0
    avg_completion_rate: float = 0
    avg_engagement_rate: float = 0
    follower_delta: int = 0


class ReviewChangeOut(BaseModel):
    metric: Literal["play", "completion_rate", "follower_delta"]
    label: str
    current: float
    previous: float | None = None
    delta_percent: float | None = None
    direction: Literal["up", "down", "flat", "baseline"]
    summary: str


class ReviewAttributionOut(BaseModel):
    content_item_id: int | None = None
    title: str
    play: int
    completion_rate: float
    engagement_rate: float
    role: Literal["driver", "opportunity"]
    reason: str


class ReviewWorkspaceOut(BaseModel):
    account: ReviewAccountOut
    period: ReviewPeriodOut
    data_status: ReviewDataStatusOut
    goal: AccountReviewGoalOut
    conclusion: str
    totals: ReviewTotalsOut
    changes: list[ReviewChangeOut]
    trend: list[TrendPoint]
    engagement: list[EngagementPoint]
    attributions: list[ReviewAttributionOut]
    evidence: list[PerformanceSnapshotOut]
    suggestions: list[OptimizationSuggestionOut]
