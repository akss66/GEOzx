"""Build an account-scoped, evidence-first operations review narrative."""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    AccountReviewGoal,
    ContentItem,
    OptimizationSuggestion,
    PlatformAccountAuth,
)
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
    ReviewSourceSummaryOut,
    ReviewTotalsOut,
    ReviewWorkspaceOut,
    TrendPoint,
)
from app.services.account_data_view import AccountDataView, AccountDataViewService

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
    current: float | None,
    previous: float | None,
    *,
    percentage_value: bool = False,
) -> ReviewChangeOut:
    if current is None:
        display_previous = (
            round(previous * 100, 2) if percentage_value and previous is not None else previous
        )
        return ReviewChangeOut(
            metric=metric,
            label=label,
            current=None,
            previous=round(display_previous, 2) if display_previous is not None else None,
            delta_percent=None,
            direction="unavailable",
            summary=f"{label}缂轰箯鐪熷疄鏁版嵁锛屾殏涓嶅仛鍛ㄦ湡瀵规瘮",
        )
    current_value = current
    direction = _direction(current_value, previous)
    delta = None
    if previous not in (None, 0):
        delta = round((current_value - previous) / abs(previous) * 100, 1)
    if direction == "baseline":
        summary = f"{label}已形成首个可比较基线"
    elif direction == "flat":
        summary = f"{label}与上一周期持平"
    else:
        verb = "提升" if direction == "up" else "下降"
        summary = f"{label}较上一周期{verb}{abs(delta or 0):.1f}%"
    display_current = (
        round(current_value * 100, 2) if percentage_value else round(current_value, 2)
    )
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
                "achievement_percent": (
                    round(totals.play / goal.target_play * 100, 1)
                    if totals.play is not None
                    else None
                ),
            }
        )
    if goal.target_completion_rate:
        components.append(
            {
                "metric": "completion_rate",
                "label": "平均完播率",
                "current": totals.avg_completion_rate,
                "target": goal.target_completion_rate,
                "achievement_percent": (
                    round(totals.avg_completion_rate / goal.target_completion_rate * 100, 1)
                    if totals.avg_completion_rate is not None
                    else None
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
                "achievement_percent": (
                    round(totals.follower_delta / goal.target_follower_delta * 100, 1)
                    if totals.follower_delta is not None
                    else None
                ),
            }
        )
    for component in components:
        if component["achievement_percent"] is None:
            component["status"] = "unavailable"
    if any(component["achievement_percent"] is None for component in components):
        return AccountReviewGoalOut(
            id=goal.id,
            period_days=goal.period_days,
            target_play=goal.target_play,
            target_completion_rate=goal.target_completion_rate,
            target_follower_delta=goal.target_follower_delta,
            status="insufficient_data",
            achievement_percent=None,
            components=components,
            summary="鐩爣宸茶缃紝閮ㄥ垎鎸囨爣灏氭湭鍥炴祦锛屾殏涓嶈绠楀畬鎴愬害",
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


def _metric_value(metrics: dict, key: str) -> int | float | None:
    metric = metrics.get(key)
    return None if metric is None else metric.value


def _daily_rollups(view: AccountDataView) -> list[dict[str, float | int | date | None]]:
    content_by_date: dict[date, list] = defaultdict(list)
    for snapshot in view.content_snapshots:
        content_by_date[snapshot.stat_date].append(snapshot)
    account_by_date = {snapshot.stat_date: snapshot for snapshot in view.account_snapshots}
    dates = sorted(set(content_by_date) | set(account_by_date))
    results: list[dict[str, float | int | date | None]] = []
    for stat_date in dates:
        account_snapshot = account_by_date.get(stat_date)
        content_snapshots = content_by_date.get(stat_date, [])
        play = _prefer_metric(
            _metric_value(account_snapshot.metrics, "play")
            if account_snapshot is not None
            else None,
            _sum_metric(content_snapshots, "play"),
        )
        exposure = _prefer_metric(
            _metric_value(account_snapshot.metrics, "exposure")
            if account_snapshot is not None
            else None,
            _sum_metric(content_snapshots, "exposure"),
        )
        follower_delta = _prefer_metric(
            _metric_value(account_snapshot.metrics, "follower_delta")
            if account_snapshot is not None
            else None,
            _sum_metric(content_snapshots, "follower_delta"),
        )
        completion_rates = _collect_metric(content_snapshots, "completion_rate")
        like_rates = _collect_metric(content_snapshots, "like_rate")
        results.append(
            {
                "date": stat_date,
                "play": play,
                "exposure": exposure,
                "follower_delta": follower_delta,
                "completion_rate": (
                    round(sum(completion_rates) / len(completion_rates), 4)
                    if completion_rates
                    else None
                ),
                "like_rate": round(sum(like_rates) / len(like_rates), 4) if like_rates else None,
            }
        )
    return results


def _prefer_metric(primary: int | float | None, fallback: int | float | None) -> int | float | None:
    return primary if primary is not None else fallback


def _sum_metric(content_snapshots: list, metric_name: str) -> int | float | None:
    values = _collect_metric(content_snapshots, metric_name)
    return sum(values) if values else None


def _collect_metric(content_snapshots: list, metric_name: str) -> list[float]:
    values: list[float] = []
    for snapshot in content_snapshots:
        value = _metric_value(snapshot.metrics, metric_name)
        if value is not None:
            values.append(float(value))
    return values


def _totals_from_view(view: AccountDataView) -> ReviewTotalsOut:
    rollups = _daily_rollups(view)
    engagement_values: list[float] = []
    for snapshot in view.content_snapshots:
        parts = [
            _metric_value(snapshot.metrics, "like_rate"),
            _metric_value(snapshot.metrics, "comment_rate"),
            _metric_value(snapshot.metrics, "share_rate"),
        ]
        present_parts = [float(part) for part in parts if part is not None]
        if present_parts:
            engagement_values.append(sum(present_parts))
    completion_values = [
        float(item["completion_rate"]) for item in rollups if item["completion_rate"] is not None
    ]
    play_values = [int(item["play"]) for item in rollups if item["play"] is not None]
    exposure_values = [int(item["exposure"]) for item in rollups if item["exposure"] is not None]
    follower_values = [
        int(item["follower_delta"]) for item in rollups if item["follower_delta"] is not None
    ]
    return ReviewTotalsOut(
        play=sum(play_values) if play_values else None,
        exposure=sum(exposure_values) if exposure_values else None,
        avg_completion_rate=(
            round(sum(completion_values) / len(completion_values), 4) if completion_values else None
        ),
        avg_engagement_rate=(
            round(sum(engagement_values) / len(engagement_values), 4) if engagement_values else None
        ),
        follower_delta=sum(follower_values) if follower_values else None,
    )


def _trend(view: AccountDataView) -> tuple[list[TrendPoint], list[EngagementPoint]]:
    rollups = _daily_rollups(view)
    trends = [
        TrendPoint(
            date=item["date"].strftime("%m/%d"),
            play=int(item["play"]) if item["play"] is not None else None,
            exposure=int(item["exposure"]) if item["exposure"] is not None else None,
        )
        for item in rollups
    ]
    engagement = [
        EngagementPoint(
            date=item["date"].strftime("%m/%d"),
            completion_rate=(
                round(float(item["completion_rate"]), 4)
                if item["completion_rate"] is not None
                else None
            ),
            like_rate=round(float(item["like_rate"]), 4) if item["like_rate"] is not None else None,
        )
        for item in rollups
    ]
    return trends, engagement


def _attributions(view: AccountDataView) -> list[ReviewAttributionOut]:
    grouped: dict[tuple[int | None, int | None, str], list] = defaultdict(list)
    for snapshot in view.content_snapshots:
        if not snapshot.has_stable_identity:
            continue
        grouped[
            (
                snapshot.platform_content_record_id,
                snapshot.content_item_id,
                snapshot.title or "未命名内容",
            )
        ].append(snapshot)

    ranked: list[tuple[int | None, str, ReviewTotalsOut]] = []
    for (platform_content_record_id, content_item_id, title), snapshots in grouped.items():
        play = sum(int(_metric_value(item.metrics, "play") or 0) for item in snapshots)
        completion = _collect_metric(snapshots, "completion_rate")
        like_rates = _collect_metric(snapshots, "like_rate")
        comment_rates = _collect_metric(snapshots, "comment_rate")
        share_rates = _collect_metric(snapshots, "share_rate")
        engagement_values = [
            like_rate + comment_rate + share_rate
            for like_rate, comment_rate, share_rate in zip(
                like_rates or [0.0] * len(snapshots),
                comment_rates or [0.0] * len(snapshots),
                share_rates or [0.0] * len(snapshots),
                strict=False,
            )
        ]
        ranked.append(
            (
                content_item_id or platform_content_record_id,
                title,
                ReviewTotalsOut(
                    play=play,
                    exposure=None,
                    avg_completion_rate=(
                        round(sum(completion) / len(completion), 4) if completion else None
                    ),
                    avg_engagement_rate=(
                        round(sum(engagement_values) / len(engagement_values), 4)
                        if engagement_values
                        else None
                    ),
                    follower_delta=None,
                ),
            )
        )
    ranked.sort(
        key=lambda item: (item[2].play or 0, item[2].avg_completion_rate or 0),
        reverse=True,
    )
    results: list[ReviewAttributionOut] = []
    for index, (content_item_id, title, totals) in enumerate(ranked[:4]):
        is_driver = index == 0
        results.append(
            ReviewAttributionOut(
                content_item_id=content_item_id,
                title=title,
                play=totals.play or 0,
                completion_rate=totals.avg_completion_rate or 0,
                engagement_rate=totals.avg_engagement_rate or 0,
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
    service = AccountDataViewService(session)
    current_view = await service.load(account, current_start, current_end)
    previous_view = await service.load(account, previous_start, previous_end)
    platform_auth = await session.scalar(
        select(PlatformAccountAuth).where(
            PlatformAccountAuth.org_id == account.org_id,
            PlatformAccountAuth.account_id == account.id,
        )
    )
    goal = await session.scalar(
        select(AccountReviewGoal).where(
            AccountReviewGoal.account_id == account.id,
            AccountReviewGoal.period_days == days,
        )
    )
    totals = _totals_from_view(current_view)
    previous_totals = _totals_from_view(previous_view) if _has_data(previous_view) else None
    goal_progress = _goal_progress(goal, totals, has_data=_has_data(current_view), days=days)
    trend, engagement = _trend(current_view)
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
    if not _has_data(current_view):
        missing_reasons.append(f"该账号近 {days} 天没有真实指标快照")
    elif (
        current_view.coverage["account_metrics"] == "available"
        and current_view.coverage["content_metrics"] == "missing"
    ):
        missing_reasons.append("当前周期仅有账号级趋势数据，作品归因尚未补齐")
    if goal is None:
        missing_reasons.append(f"尚未设置近 {days} 天运营目标")
    if account.data_sync_status != "healthy":
        missing_reasons.append(
            f"账号数据回流状态为：{_data_sync_status_label(account.data_sync_status)}"
        )

    if not _has_data(current_view):
        conclusion = "尚未形成可复盘的数据周期，先完成账号数据同步。"
    elif goal_progress.achievement_percent is not None:
        goal_note = (
            "已达到周期目标。"
            if goal_progress.status == "achieved"
            else "下一轮仍需围绕差距推进。"
        )
        conclusion = f"{changes[0].summary}；{goal_progress.summary}，{goal_note}"
    else:
        conclusion = f"{changes[0].summary}，但尚未设置周期目标，暂不能判断目标完成度。"

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
            has_data=_has_data(current_view),
            sources=_review_sources(current_view),
            latest_stat_date=current_view.freshness.latest_observed_at,
            latest_synced_at=(
                platform_auth.last_sync_at
                if platform_auth is not None and platform_auth.last_sync_at is not None
                else current_view.latest_synced_at
            ),
            latest_confirmed_at=current_view.latest_confirmed_at,
            coverage=current_view.coverage,
            conflict_count=len(current_view.conflicts),
            source_summary=[
                ReviewSourceSummaryOut(
                    batch_id=item.batch_id,
                    source_kind=item.source_kind,
                    data_domains=item.data_domains,
                    confirmed_at=item.confirmed_at,
                    period_start=item.period_start,
                    period_end=item.period_end,
                )
                for item in current_view.source_summary
            ],
            missing_reasons=missing_reasons,
        ),
        goal=goal_progress,
        conclusion=conclusion,
        totals=totals,
        changes=changes,
        trend=trend,
        engagement=engagement,
        attributions=_attributions(current_view),
        evidence=[
            PerformanceSnapshotOut.model_validate(row) for row in current_view.evidence_rows[:12]
        ],
        suggestions=await _suggestions(
            session,
            org_id=account.org_id,
            account_id=account.id,
        ),
    )


def _has_data(view: AccountDataView) -> bool:
    return bool(view.account_snapshots or view.content_snapshots)


def _review_sources(view: AccountDataView) -> list[str]:
    content_sources = sorted({row.source.value for row in view.evidence_rows})
    if content_sources:
        return content_sources
    return sorted({item.source_kind for item in view.source_summary})
