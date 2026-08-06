from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.account_data_view import (
    AccountDataMetric,
    AccountDataObservation,
    AccountDataView,
    AccountMetricSnapshotView,
    ContentMetricSnapshotView,
)

Aggregation = Literal["sum", "latest", "average"]
ComparisonMode = Literal["none", "previous_period"]
Direction = Literal["up", "down", "flat", "unavailable"]
RankingMode = Literal["top", "bottom", "both"]
AnswerabilityStatus = Literal["sufficient", "partial", "insufficient"]
ANOMALY_RELATIVE_CHANGE_THRESHOLD = 0.2


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    code: str
    label: str
    unit: str
    aggregation: Aggregation
    minimum_samples: int = 1


def _metric(
    code: str,
    label: str,
    unit: str,
    aggregation: Aggregation,
) -> MetricDefinition:
    return MetricDefinition(
        code=code,
        label=label,
        unit=unit,
        aggregation=aggregation,
    )


METRIC_REGISTRY: Mapping[str, MetricDefinition] = MappingProxyType(
    {
        "play": _metric("play", "播放量", "count", "sum"),
        "exposure": _metric("exposure", "曝光量", "count", "sum"),
        "follower_count": _metric("follower_count", "粉丝数", "count", "latest"),
        "follower_delta": _metric("follower_delta", "净增粉丝", "count", "sum"),
        "engagement_rate": _metric("engagement_rate", "互动率", "percent", "average"),
        "profile_visit_count": _metric("profile_visit_count", "主页访问", "count", "sum"),
        "unfollow_count": _metric("unfollow_count", "取关粉丝", "count", "sum"),
        "retention_rate": _metric("retention_rate", "留存率", "percent", "average"),
        "like_count": _metric("like_count", "点赞量", "count", "sum"),
        "comment_count": _metric("comment_count", "评论量", "count", "sum"),
        "share_count": _metric("share_count", "分享量", "count", "sum"),
        "favorite_count": _metric("favorite_count", "收藏量", "count", "sum"),
        "cover_click_rate": _metric("cover_click_rate", "封面点击率", "percent", "average"),
        "completion_rate": _metric("completion_rate", "完播率", "percent", "average"),
        "like_rate": _metric("like_rate", "点赞率", "percent", "average"),
        "comment_rate": _metric("comment_rate", "评论率", "percent", "average"),
        "share_rate": _metric("share_rate", "分享率", "percent", "average"),
        "avg_watch_time_seconds": _metric(
            "avg_watch_time_seconds", "平均观看时长", "seconds", "average"
        ),
        "completion_rate_5s": _metric("completion_rate_5s", "5秒完播率", "percent", "average"),
        "bounce_rate_2s": _metric("bounce_rate_2s", "2秒跳出率", "percent", "average"),
    }
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DateRange(FrozenModel):
    days: int = Field(ge=1)
    start: date
    end: date


class BusinessEvidenceRef(FrozenModel):
    source_type: str
    source_id: str
    account_id: int
    batch_id: int | None = None
    metric_code: str
    period_start: date
    period_end: date
    observed_at: date
    value: int | float
    unit: str
    content_hash: str


class TrendPoint(FrozenModel):
    stat_date: date
    value: int | float
    direction_from_previous: Direction
    evidence_hash: str


class AnalysisFact(FrozenModel):
    metric_code: str
    label: str
    unit: str
    current_value: int | float
    previous_value: int | float | None = None
    absolute_change: int | float | None = None
    relative_change: float | None = None
    direction: Direction
    current_period: DateRange
    comparison_period: DateRange | None = None
    sample_count: int = Field(ge=1)
    evidence_hashes: list[str]
    aggregation_note: str = ""
    daily_trend: list[TrendPoint] = Field(default_factory=list)
    latest_direction: Direction = "unavailable"
    latest_direction_started_at: date | None = None
    change_rank: int | None = Field(default=None, ge=1)
    anomaly_flags: list[str] = Field(default_factory=list)


class ContentRanking(FrozenModel):
    content_item_id: int | None
    platform_content_record_id: int | None
    title: str | None
    metric_code: str
    value: int | float
    rank_kind: Literal["top", "bottom"]
    stat_date: date
    evidence_hashes: list[str]


class DataQualitySummary(FrozenModel):
    latest_observed_at: date | None
    days_since_observed: int | None
    conflict_count: int = Field(ge=0)
    current_sample_count: int = Field(ge=0)
    comparison_sample_count: int = Field(ge=0)


class Answerability(FrozenModel):
    status: AnswerabilityStatus
    confidence: float = Field(ge=0, le=1)
    supported_claims: list[str]
    unsupported_claims: list[str]
    missing_metrics: list[str]
    missing_periods: list[str]
    reasons: list[str]


class AccountMetricAnalysis(FrozenModel):
    account_id: int
    query_window: DateRange
    comparison_window: DateRange | None
    answerability: Answerability
    facts: list[AnalysisFact]
    content_rankings: list[ContentRanking]
    data_quality: DataQualitySummary
    evidence_refs: list[BusinessEvidenceRef]


@dataclass(frozen=True, slots=True)
class _MetricSample:
    stat_date: date
    value: int | float
    observation: AccountDataObservation


def analyze_account_metrics(
    view: AccountDataView,
    *,
    account_id: int,
    days: int,
    comparison: ComparisonMode,
    metric_codes: Sequence[str],
    top_n: int,
    ranking_mode: RankingMode = "both",
    require_daily_trend: bool = False,
    today: date | None = None,
) -> AccountMetricAnalysis:
    if days < 1:
        raise ValueError("days must be at least 1")
    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    if comparison not in {"none", "previous_period"}:
        raise ValueError(f"unsupported comparison: {comparison}")
    if ranking_mode not in {"top", "bottom", "both"}:
        raise ValueError(f"unsupported ranking mode: {ranking_mode}")

    requested_metrics = _validate_metric_codes(metric_codes)
    period_end = today or date.today()
    period_start = period_end - timedelta(days=days - 1)
    current_period = DateRange(days=days, start=period_start, end=period_end)
    comparison_period = (
        DateRange(
            days=days,
            start=period_start - timedelta(days=days),
            end=period_start - timedelta(days=1),
        )
        if comparison == "previous_period"
        else None
    )

    facts: list[AnalysisFact] = []
    evidence_refs: list[BusinessEvidenceRef] = []
    supported_claims: list[str] = []
    unsupported_claims: list[str] = []
    missing_metrics: list[str] = []
    missing_periods: list[str] = []
    current_sample_count = 0
    comparison_sample_count = 0

    for metric_code in requested_metrics:
        definition = METRIC_REGISTRY[metric_code]
        current_samples = _samples_for_period(view, metric_code, current_period)
        previous_samples = (
            _samples_for_period(view, metric_code, comparison_period)
            if comparison_period is not None
            else []
        )
        current_sample_count += len(current_samples)
        comparison_sample_count += len(previous_samples)
        if not current_samples:
            missing_metrics.append(metric_code)
            unsupported_claims.append(f"{metric_code}:current")
            continue

        current_value = _aggregate(current_samples, definition.aggregation)
        previous_value = (
            _aggregate(previous_samples, definition.aggregation) if previous_samples else None
        )
        absolute_change = current_value - previous_value if previous_value is not None else None
        relative_change = (
            absolute_change / previous_value
            if absolute_change is not None and previous_value != 0
            else None
        )
        direction = _direction(absolute_change)

        fact_evidence = [
            _evidence_ref(
                sample,
                account_id=account_id,
                metric=definition,
            )
            for sample in (*current_samples, *previous_samples)
        ]
        evidence_refs.extend(fact_evidence)
        supported_claims.append(f"{metric_code}:current")
        if comparison_period is not None:
            if previous_samples:
                supported_claims.append(f"{metric_code}:trend")
            else:
                unsupported_claims.append(f"{metric_code}:trend")
                missing_periods.append(f"{metric_code}:previous_period")

        daily_trend = _daily_trend(
            current_samples,
            account_id=account_id,
            metric=definition,
        )
        latest_direction, latest_direction_started_at = _latest_direction_start(daily_trend)
        if require_daily_trend:
            claim = f"{metric_code}:daily_trend"
            if len(daily_trend) >= 2:
                supported_claims.append(claim)
            else:
                unsupported_claims.append(claim)

        facts.append(
            AnalysisFact(
                metric_code=metric_code,
                label=definition.label,
                unit=definition.unit,
                current_value=current_value,
                previous_value=previous_value,
                absolute_change=absolute_change,
                relative_change=relative_change,
                direction=direction,
                current_period=current_period,
                comparison_period=comparison_period,
                sample_count=len(current_samples),
                evidence_hashes=[item.content_hash for item in fact_evidence],
                aggregation_note=_aggregation_note(definition.aggregation),
                daily_trend=daily_trend,
                latest_direction=latest_direction,
                latest_direction_started_at=latest_direction_started_at,
                anomaly_flags=(
                    ["period_relative_change_ge_20_percent"]
                    if relative_change is not None
                    and abs(relative_change) >= ANOMALY_RELATIVE_CHANGE_THRESHOLD
                    else []
                ),
            )
        )

    facts = _rank_metric_changes(facts)
    evidence_refs = _unique_sorted_evidence(evidence_refs)
    content_rankings = _content_rankings(
        view,
        account_id=account_id,
        metric_codes=requested_metrics,
        current_period=current_period,
        top_n=top_n,
        ranking_mode=ranking_mode,
    )
    for ranking in content_rankings:
        for content_hash in ranking.evidence_hashes:
            if not any(item.content_hash == content_hash for item in evidence_refs):
                evidence = _ranking_evidence(
                    view,
                    account_id=account_id,
                    ranking=ranking,
                )
                if evidence is not None:
                    evidence_refs.append(evidence)
    evidence_refs = _unique_sorted_evidence(evidence_refs)

    answerability = _answerability(
        facts=facts,
        comparison=comparison,
        supported_claims=supported_claims,
        unsupported_claims=unsupported_claims,
        missing_metrics=missing_metrics,
        missing_periods=missing_periods,
        conflict_count=len(view.conflicts),
        days_since_observed=view.freshness.days_since_observed,
    )
    return AccountMetricAnalysis(
        account_id=account_id,
        query_window=current_period,
        comparison_window=comparison_period,
        answerability=answerability,
        facts=facts,
        content_rankings=content_rankings,
        data_quality=DataQualitySummary(
            latest_observed_at=view.freshness.latest_observed_at,
            days_since_observed=view.freshness.days_since_observed,
            conflict_count=len(view.conflicts),
            current_sample_count=current_sample_count,
            comparison_sample_count=comparison_sample_count,
        ),
        evidence_refs=evidence_refs,
    )


def _validate_metric_codes(metric_codes: Sequence[str]) -> list[str]:
    results: list[str] = []
    for metric_code in metric_codes:
        if metric_code not in METRIC_REGISTRY:
            raise ValueError(f"unsupported metric: {metric_code}")
        if metric_code not in results:
            results.append(metric_code)
    if not results:
        raise ValueError("at least one metric is required")
    return results


def _samples_for_period(
    view: AccountDataView,
    metric_code: str,
    period: DateRange | None,
) -> list[_MetricSample]:
    if period is None:
        return []
    account_samples = _account_samples(view.account_snapshots, metric_code, period)
    if account_samples:
        return account_samples
    return _content_samples(view.content_snapshots, metric_code, period)


def _account_samples(
    snapshots: Sequence[AccountMetricSnapshotView],
    metric_code: str,
    period: DateRange,
) -> list[_MetricSample]:
    return _collect_samples(
        (snapshot.stat_date, snapshot.metrics.get(metric_code))
        for snapshot in snapshots
        if period.start <= snapshot.stat_date <= period.end
    )


def _content_samples(
    snapshots: Sequence[ContentMetricSnapshotView],
    metric_code: str,
    period: DateRange,
) -> list[_MetricSample]:
    return _collect_samples(
        (snapshot.stat_date, snapshot.metrics.get(metric_code))
        for snapshot in snapshots
        if period.start <= snapshot.stat_date <= period.end
    )


def _collect_samples(
    rows: Sequence[tuple[date, AccountDataMetric | None]]
    | Sequence[tuple[date, AccountDataMetric]],
) -> list[_MetricSample]:
    samples: list[_MetricSample] = []
    for stat_date, metric in rows:
        if metric is None or metric.value is None:
            continue
        observation = next(
            (
                item
                for item in metric.observations
                if item.value is not None and item.value == metric.value
            ),
            None,
        )
        if observation is None:
            continue
        samples.append(
            _MetricSample(
                stat_date=stat_date,
                value=metric.value,
                observation=observation,
            )
        )
    return sorted(
        samples,
        key=lambda item: (
            item.stat_date,
            item.observation.evidence_kind,
            item.observation.evidence_id,
        ),
    )


def _aggregate(samples: Sequence[_MetricSample], aggregation: Aggregation) -> int | float:
    if aggregation == "latest":
        return samples[-1].value
    total = sum(item.value for item in samples)
    if aggregation == "average":
        return total / len(samples)
    return total


def _aggregation_note(aggregation: Aggregation) -> str:
    return {
        "sum": "周期内已确认样本求和",
        "latest": "采用周期末最新确认值",
        "average": "缺少可核验分母，采用已确认样本的简单均值",
    }[aggregation]


def _direction(change: int | float | None) -> Direction:
    if change is None:
        return "unavailable"
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def _daily_trend(
    samples: Sequence[_MetricSample],
    *,
    account_id: int,
    metric: MetricDefinition,
) -> list[TrendPoint]:
    points: list[TrendPoint] = []
    previous_value: int | float | None = None
    for sample in samples:
        evidence = _evidence_ref(sample, account_id=account_id, metric=metric)
        points.append(
            TrendPoint(
                stat_date=sample.stat_date,
                value=sample.value,
                direction_from_previous=_direction(
                    sample.value - previous_value if previous_value is not None else None
                ),
                evidence_hash=evidence.content_hash,
            )
        )
        previous_value = sample.value
    return points


def _latest_direction_start(points: Sequence[TrendPoint]) -> tuple[Direction, date | None]:
    if len(points) < 2:
        return "unavailable", None
    latest_direction = points[-1].direction_from_previous
    if latest_direction == "unavailable":
        return latest_direction, None
    started_at = points[-1].stat_date
    for point in reversed(points[1:-1]):
        if point.direction_from_previous != latest_direction:
            break
        started_at = point.stat_date
    return latest_direction, started_at


def _rank_metric_changes(facts: Sequence[AnalysisFact]) -> list[AnalysisFact]:
    ranked = sorted(
        (fact for fact in facts if fact.relative_change is not None),
        key=lambda fact: (-abs(float(fact.relative_change or 0)), fact.metric_code),
    )
    ranks = {fact.metric_code: index for index, fact in enumerate(ranked, start=1)}
    return [fact.model_copy(update={"change_rank": ranks.get(fact.metric_code)}) for fact in facts]


def _evidence_ref(
    sample: _MetricSample,
    *,
    account_id: int,
    metric: MetricDefinition,
) -> BusinessEvidenceRef:
    source = sample.observation.source
    source_type = source.value if hasattr(source, "value") else str(source)
    payload = {
        "account_id": account_id,
        "evidence_id": sample.observation.evidence_id,
        "evidence_kind": sample.observation.evidence_kind,
        "metric_code": metric.code,
        "observed_at": sample.observation.observed_at.isoformat(),
        "source_type": source_type,
        "unit": metric.unit,
        "value": sample.value,
    }
    content_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return BusinessEvidenceRef(
        source_type=source_type,
        source_id=(f"{sample.observation.evidence_kind}:{sample.observation.evidence_id}"),
        account_id=account_id,
        metric_code=metric.code,
        period_start=sample.stat_date,
        period_end=sample.stat_date,
        observed_at=sample.observation.observed_at,
        value=sample.value,
        unit=metric.unit,
        content_hash=content_hash,
    )


def _unique_sorted_evidence(
    evidence_refs: Sequence[BusinessEvidenceRef],
) -> list[BusinessEvidenceRef]:
    unique = {item.content_hash: item for item in evidence_refs}
    return sorted(
        unique.values(),
        key=lambda item: (
            item.metric_code,
            item.observed_at,
            item.source_type,
            item.source_id,
        ),
    )


def _content_rankings(
    view: AccountDataView,
    *,
    account_id: int,
    metric_codes: Sequence[str],
    current_period: DateRange,
    top_n: int,
    ranking_mode: RankingMode,
) -> list[ContentRanking]:
    candidates: list[tuple[ContentMetricSnapshotView, str, _MetricSample]] = []
    for metric_code in metric_codes:
        for snapshot in view.content_snapshots:
            if not current_period.start <= snapshot.stat_date <= current_period.end:
                continue
            metric = snapshot.metrics.get(metric_code)
            samples = _collect_samples([(snapshot.stat_date, metric)])
            if samples:
                candidates.append((snapshot, metric_code, samples[0]))
        if candidates:
            break
    if not candidates:
        return []

    descending = sorted(candidates, key=lambda item: item[2].value, reverse=True)
    if ranking_mode == "top":
        selected = [(*item, "top") for item in descending[:top_n]]
    elif ranking_mode == "bottom":
        selected = [(*item, "bottom") for item in reversed(descending[-top_n:])]
    else:
        top_count = (top_n + 1) // 2
        bottom_count = top_n - top_count
        selected = [(*item, "top") for item in descending[:top_count]]
        selected_ids = {_content_identity(item[0]) for item in selected}
        for item in reversed(descending):
            if sum(entry[3] == "bottom" for entry in selected) >= bottom_count:
                break
            if _content_identity(item[0]) in selected_ids:
                continue
            selected.append((*item, "bottom"))
            selected_ids.add(_content_identity(item[0]))

    results: list[ContentRanking] = []
    for snapshot, metric_code, sample, rank_kind in selected:
        evidence = _evidence_ref(
            sample,
            account_id=account_id,
            metric=METRIC_REGISTRY[metric_code],
        )
        results.append(
            ContentRanking(
                content_item_id=snapshot.content_item_id,
                platform_content_record_id=snapshot.platform_content_record_id,
                title=snapshot.title,
                metric_code=metric_code,
                value=sample.value,
                rank_kind=rank_kind,
                stat_date=snapshot.stat_date,
                evidence_hashes=[evidence.content_hash],
            )
        )
    return results


def _content_identity(snapshot: ContentMetricSnapshotView) -> tuple[object, ...]:
    return (
        snapshot.platform_content_record_id,
        snapshot.content_item_id,
        snapshot.stat_date,
        snapshot.title,
    )


def _ranking_evidence(
    view: AccountDataView,
    *,
    account_id: int,
    ranking: ContentRanking,
) -> BusinessEvidenceRef | None:
    for snapshot in view.content_snapshots:
        if (
            snapshot.content_item_id != ranking.content_item_id
            or snapshot.platform_content_record_id != ranking.platform_content_record_id
            or snapshot.stat_date != ranking.stat_date
        ):
            continue
        samples = _collect_samples(
            [(snapshot.stat_date, snapshot.metrics.get(ranking.metric_code))]
        )
        if samples:
            return _evidence_ref(
                samples[0],
                account_id=account_id,
                metric=METRIC_REGISTRY[ranking.metric_code],
            )
    return None


def _answerability(
    *,
    facts: Sequence[AnalysisFact],
    comparison: ComparisonMode,
    supported_claims: list[str],
    unsupported_claims: list[str],
    missing_metrics: list[str],
    missing_periods: list[str],
    conflict_count: int,
    days_since_observed: int | None,
) -> Answerability:
    reasons: list[str] = []
    if not facts:
        reasons.append("当前指标和时间范围内没有已确认数据。")
        return Answerability(
            status="insufficient",
            confidence=0,
            supported_claims=supported_claims,
            unsupported_claims=unsupported_claims,
            missing_metrics=missing_metrics,
            missing_periods=missing_periods,
            reasons=reasons,
        )

    status: AnswerabilityStatus = "sufficient"
    confidence = 0.9 if comparison == "previous_period" else 0.8
    if unsupported_claims or missing_metrics or missing_periods:
        status = "partial"
        confidence = min(confidence, 0.55)
        reasons.append("部分分析要求缺少已确认数据，当前只能给出有限结论。")
    if conflict_count:
        confidence = max(0, confidence - 0.15)
        reasons.append(f"仍有 {conflict_count} 项数据冲突未解决，会降低结论可信度。")
    if days_since_observed is not None and days_since_observed > 7:
        confidence = max(0, confidence - 0.15)
        reasons.append("最新一条已确认数据距今超过 7 天，结论可能无法反映当前状态。")
    return Answerability(
        status=status,
        confidence=round(confidence, 2),
        supported_claims=supported_claims,
        unsupported_claims=unsupported_claims,
        missing_metrics=missing_metrics,
        missing_periods=missing_periods,
        reasons=reasons,
    )
