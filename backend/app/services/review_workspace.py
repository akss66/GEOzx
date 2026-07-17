"""Build an account-scoped, evidence-first operations review narrative."""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    AccountReviewGoal,
    ContentItem,
    MetricSnapshot,
    OptimizationSuggestion,
)
from app.models.enums import MetricSource
from app.schemas.feedback import OptimizationSuggestionOut
from app.schemas.metrics import (
    AccountReviewGoalOut,
    EngagementPoint,
    PerformanceSnapshotOut,
    ReviewAccountOut,
    ReviewAttributionOut,
    ReviewChangeOut,
    ReviewDataStatusOut,
    ReviewPeriodOut,
    ReviewTotalsOut,
    ReviewWorkspaceOut,
    TrendPoint,
)

_DATA_SYNC_STATUS_LABELS = {
    "not_configured": "尚未配置",
    "pending": "等待首次同步",
    "syncing": "正在同步",
    "healthy": "正常",
    "failed": "同步失败",
    "manual": "仅支持手动录入",
}


def _data_sync_status_label(status: str) -> str:
    return _DATA_SYNC_STATUS_LABELS.get(status, "状态待确认")


def _totals(rows: list[MetricSnapshot]) -> ReviewTotalsOut:
    count = len(rows)
    return ReviewTotalsOut(
        play=sum(row.play for row in rows),
        exposure=sum(row.exposure for row in rows),
        avg_completion_rate=round(
            sum(row.completion_rate for row in rows) / count if count else 0,
            4,
        ),
        avg_engagement_rate=round(
            sum(row.like_rate + row.comment_rate + row.share_rate for row in rows) / count
            if count
            else 0,
            4,
        ),
        follower_delta=sum(row.follower_delta for row in rows),
    )


def _direction(current: float, previous: float | None) -> str:
    if previous is None or previous == 0:
        return "baseline"
    if current > previous:
        return "up"
    if current < previous:
        return "down"
    return "flat"


def _change(
    metric: str,
    label: str,
    current: float,
    previous: float | None,
    *,
    percentage_value: bool = False,
) -> ReviewChangeOut:
    direction = _direction(current, previous)
    delta = None
    if previous not in (None, 0):
        delta = round((current - previous) / abs(previous) * 100, 1)
    if direction == "baseline":
        summary = f"{label}已形成首个可比较基线"
    elif direction == "flat":
        summary = f"{label}与上一周期持平"
    else:
        verb = "提升" if direction == "up" else "下降"
        summary = f"{label}较上一周期{verb}{abs(delta or 0):.1f}%"
    display_current = round(current * 100, 2) if percentage_value else round(current, 2)
    display_previous = (
        round(previous * 100, 2) if percentage_value and previous is not None else previous
    )
    return ReviewChangeOut(
        metric=metric,
        label=label,
        current=display_current,
        previous=round(display_previous, 2) if display_previous is not None else None,
        delta_percent=delta,
        direction=direction,
        summary=summary,
    )


def _goal_progress(
    goal: AccountReviewGoal | None,
    totals: ReviewTotalsOut,
    *,
    has_data: bool,
    days: int,
) -> AccountReviewGoalOut:
    if goal is None:
        return AccountReviewGoalOut(
            period_days=days,
            status="not_configured",
            summary=f"尚未设置近 {days} 天运营目标",
        )
    if not has_data:
        return AccountReviewGoalOut(
            id=goal.id,
            period_days=goal.period_days,
            target_play=goal.target_play,
            target_completion_rate=goal.target_completion_rate,
            target_follower_delta=goal.target_follower_delta,
            status="insufficient_data",
            summary="目标已设置，等待真实指标回流后计算完成度",
        )

    components: list[dict] = []
    if goal.target_play:
        components.append(
            {
                "metric": "play",
                "label": "播放量",
                "current": totals.play,
                "target": goal.target_play,
                "achievement_percent": round(totals.play / goal.target_play * 100, 1),
            }
        )
    if goal.target_completion_rate:
        components.append(
            {
                "metric": "completion_rate",
                "label": "平均完播率",
                "current": totals.avg_completion_rate,
                "target": goal.target_completion_rate,
                "achievement_percent": round(
                    totals.avg_completion_rate / goal.target_completion_rate * 100,
                    1,
                ),
            }
        )
    if goal.target_follower_delta:
        components.append(
            {
                "metric": "follower_delta",
                "label": "净增粉丝",
                "current": totals.follower_delta,
                "target": goal.target_follower_delta,
                "achievement_percent": round(
                    totals.follower_delta / goal.target_follower_delta * 100,
                    1,
                ),
            }
        )
    achievement = round(
        sum(component["achievement_percent"] for component in components) / len(components),
        1,
    )
    status = "achieved" if achievement >= 100 else "on_track" if achievement >= 80 else "behind"
    return AccountReviewGoalOut(
        id=goal.id,
        period_days=goal.period_days,
        target_play=goal.target_play,
        target_completion_rate=goal.target_completion_rate,
        target_follower_delta=goal.target_follower_delta,
        status=status,
        achievement_percent=achievement,
        components=components,
        summary=f"近 {days} 天目标整体完成 {achievement:.1f}%",
    )


def _trend(rows: list[MetricSnapshot]) -> tuple[list[TrendPoint], list[EngagementPoint]]:
    grouped: dict[date, list[MetricSnapshot]] = defaultdict(list)
    for row in rows:
        grouped[row.stat_date].append(row)
    trends: list[TrendPoint] = []
    engagement: list[EngagementPoint] = []
    for stat_date in sorted(grouped):
        daily = grouped[stat_date]
        trends.append(
            TrendPoint(
                date=stat_date.strftime("%m/%d"),
                play=sum(row.play for row in daily),
                exposure=sum(row.exposure for row in daily),
            )
        )
        engagement.append(
            EngagementPoint(
                date=stat_date.strftime("%m/%d"),
                completion_rate=round(
                    sum(row.completion_rate for row in daily) / len(daily),
                    4,
                ),
                like_rate=round(sum(row.like_rate for row in daily) / len(daily), 4),
            )
        )
    return trends, engagement


def _attributions(rows: list[MetricSnapshot]) -> list[ReviewAttributionOut]:
    grouped: dict[tuple[int | None, str], list[MetricSnapshot]] = defaultdict(list)
    for row in rows:
        grouped[(row.content_item_id, row.title or "未命名内容")].append(row)
    ranked: list[tuple[int | None, str, ReviewTotalsOut]] = [
        (content_item_id, title, _totals(items))
        for (content_item_id, title), items in grouped.items()
    ]
    ranked.sort(key=lambda item: (item[2].play, item[2].avg_completion_rate), reverse=True)
    results: list[ReviewAttributionOut] = []
    for index, (content_item_id, title, totals) in enumerate(ranked[:4]):
        is_driver = index == 0
        results.append(
            ReviewAttributionOut(
                content_item_id=content_item_id,
                title=title,
                play=totals.play,
                completion_rate=totals.avg_completion_rate,
                engagement_rate=totals.avg_engagement_rate,
                role="driver" if is_driver else "opportunity",
                reason=(
                    "本周期播放贡献最高，是当前增长驱动内容"
                    if is_driver
                    else "相对头部内容仍有优化空间，可作为下一轮对照样本"
                ),
            )
        )
    return results


async def _suggestions(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
) -> list[OptimizationSuggestionOut]:
    rows = (
        await session.execute(
            select(OptimizationSuggestion, ContentItem.title)
            .join(ContentItem, OptimizationSuggestion.content_item_id == ContentItem.id)
            .where(
                OptimizationSuggestion.org_id == org_id,
                ContentItem.account_id == account_id,
            )
            .order_by(OptimizationSuggestion.id.desc())
            .limit(20)
        )
    ).all()
    return [
        OptimizationSuggestionOut(
            id=row.id,
            content_item_id=row.content_item_id,
            content_title=title,
            source_deliverable_id=row.source_deliverable_id,
            target_stage=row.target_stage,
            suggestion=row.suggestion,
            status=row.status,
            note=row.note,
            accepted_at=row.accepted_at,
            verified_at=row.verified_at,
            created_at=row.created_at,
        )
        for row, title in rows
    ]


async def build_review_workspace(
    session: AsyncSession,
    *,
    account: Account,
    days: int,
) -> ReviewWorkspaceOut:
    current_end = date.today()
    current_start = current_end - timedelta(days=days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    base = (
        MetricSnapshot.org_id == account.org_id,
        MetricSnapshot.account_id == account.id,
        MetricSnapshot.source != MetricSource.DEMO,
    )
    current_rows = list(
        await session.scalars(
            select(MetricSnapshot)
            .where(
                *base,
                MetricSnapshot.stat_date.between(current_start, current_end),
            )
            .order_by(MetricSnapshot.stat_date.desc(), MetricSnapshot.id.desc())
        )
    )
    previous_rows = list(
        await session.scalars(
            select(MetricSnapshot).where(
                *base,
                MetricSnapshot.stat_date.between(previous_start, previous_end),
            )
        )
    )
    goal = await session.scalar(
        select(AccountReviewGoal).where(
            AccountReviewGoal.account_id == account.id,
            AccountReviewGoal.period_days == days,
        )
    )
    totals = _totals(current_rows)
    previous_totals = _totals(previous_rows) if previous_rows else None
    goal_progress = _goal_progress(goal, totals, has_data=bool(current_rows), days=days)
    trend, engagement = _trend(current_rows)
    changes = [
        _change("play", "播放量", totals.play, previous_totals.play if previous_totals else None),
        _change(
            "completion_rate",
            "平均完播率",
            totals.avg_completion_rate,
            previous_totals.avg_completion_rate if previous_totals else None,
            percentage_value=True,
        ),
        _change(
            "follower_delta",
            "净增粉丝",
            totals.follower_delta,
            previous_totals.follower_delta if previous_totals else None,
        ),
    ]

    missing_reasons: list[str] = []
    if not current_rows:
        missing_reasons.append(f"该账号近 {days} 天没有真实指标快照")
    if goal is None:
        missing_reasons.append(f"尚未设置近 {days} 天运营目标")
    if account.data_sync_status != "healthy":
        missing_reasons.append(
            f"账号数据回流状态为：{_data_sync_status_label(account.data_sync_status)}"
        )

    if not current_rows:
        conclusion = "尚未形成可复盘的数据周期，先完成账号数据同步。"
    elif goal_progress.achievement_percent is not None:
        goal_note = (
            "已达到周期目标。"
            if goal_progress.status == "achieved"
            else "下一轮仍需围绕差距推进。"
        )
        conclusion = (
            f"{changes[0].summary}；{goal_progress.summary}，"
            f"{goal_note}"
        )
    else:
        conclusion = f"{changes[0].summary}，但尚未设置周期目标，暂不能判断目标完成度。"

    latest = current_rows[0] if current_rows else None
    return ReviewWorkspaceOut(
        account=ReviewAccountOut(
            id=account.id,
            nickname=account.nickname,
            platform=account.platform.value,
            auth_status=account.auth_status,
            data_sync_status=account.data_sync_status,
        ),
        period=ReviewPeriodOut(
            days=days,
            current_start=current_start,
            current_end=current_end,
            previous_start=previous_start,
            previous_end=previous_end,
        ),
        data_status=ReviewDataStatusOut(
            has_data=bool(current_rows),
            sources=sorted({row.source for row in current_rows}, key=lambda source: source.value),
            latest_stat_date=latest.stat_date if latest else None,
            latest_synced_at=max((row.updated_at for row in current_rows), default=None),
            missing_reasons=missing_reasons,
        ),
        goal=goal_progress,
        conclusion=conclusion,
        totals=totals,
        changes=changes,
        trend=trend,
        engagement=engagement,
        attributions=_attributions(current_rows),
        evidence=[PerformanceSnapshotOut.model_validate(row) for row in current_rows[:12]],
        suggestions=await _suggestions(
            session,
            org_id=account.org_id,
            account_id=account.id,
        ),
    )
