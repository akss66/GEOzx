from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.models.enums import DataSourceKind
from app.services.account_data_view import (
    AccountDataFreshness,
    AccountDataMetric,
    AccountDataObservation,
    AccountDataView,
    AccountMetricSnapshotView,
    ConflictView,
    ContentMetricSnapshotView,
)
from app.services.account_metric_analysis import (
    METRIC_REGISTRY,
    analyze_account_metrics,
)


def _metric(
    code: str,
    value: int | float,
    *,
    observed_at: date,
    evidence_id: int,
) -> AccountDataMetric:
    return AccountDataMetric(
        metric=code,
        value=value,
        source=DataSourceKind.PLATFORM_EXPORT,
        observations=[
            AccountDataObservation(
                metric=code,
                value=value,
                source=DataSourceKind.PLATFORM_EXPORT,
                observed_at=observed_at,
                confirmed_at=datetime(2026, 8, 5, tzinfo=UTC),
                evidence_id=evidence_id,
                evidence_kind="account_metric_snapshot",
            )
        ],
    )


def _view(
    *,
    account_rows: list[tuple[str, dict[str, int | float]]] | None = None,
    content_rows: list[tuple[str, str, dict[str, int | float]]] | None = None,
    conflicts: list[ConflictView] | None = None,
) -> AccountDataView:
    account_snapshots: list[AccountMetricSnapshotView] = []
    for evidence_id, (raw_date, values) in enumerate(account_rows or [], start=1):
        observed_at = date.fromisoformat(raw_date)
        account_snapshots.append(
            AccountMetricSnapshotView(
                stat_date=observed_at,
                metrics={
                    code: _metric(
                        code,
                        value,
                        observed_at=observed_at,
                        evidence_id=evidence_id,
                    )
                    for code, value in values.items()
                },
            )
        )

    content_snapshots: list[ContentMetricSnapshotView] = []
    for offset, (raw_date, title, values) in enumerate(content_rows or [], start=101):
        observed_at = date.fromisoformat(raw_date)
        content_snapshots.append(
            ContentMetricSnapshotView(
                stat_date=observed_at,
                title=title,
                content_item_id=offset,
                platform_content_record_id=offset,
                has_stable_identity=True,
                content_format="short_video",
                review_status=None,
                metrics={
                    code: _metric(
                        code,
                        value,
                        observed_at=observed_at,
                        evidence_id=offset,
                    )
                    for code, value in values.items()
                },
            )
        )

    observed_dates = [item.stat_date for item in (*account_snapshots, *content_snapshots)]
    latest_observed_at = max(observed_dates, default=None)
    confirmed_at = datetime(2026, 8, 5, tzinfo=UTC) if observed_dates else None
    return AccountDataView(
        coverage={},
        freshness=AccountDataFreshness(
            latest_observed_at=latest_observed_at,
            latest_confirmed_at=confirmed_at,
            days_since_observed=(
                (date(2026, 8, 5) - latest_observed_at).days
                if latest_observed_at is not None
                else None
            ),
            days_since_confirmed=0 if confirmed_at is not None else None,
        ),
        conflicts=list(conflicts or []),
        content_snapshots=content_snapshots,
        account_snapshots=account_snapshots,
        audience=[],
        benchmarks=[],
        evidence_rows=[],
        latest_synced_at=None,
        latest_confirmed_at=confirmed_at,
        source_summary=[],
    )


def test_metric_registry_freezes_supported_aggregation_and_units() -> None:
    assert METRIC_REGISTRY["play"].aggregation == "sum"
    assert METRIC_REGISTRY["follower_count"].aggregation == "latest"
    assert METRIC_REGISTRY["completion_rate"].unit == "percent"


def test_analysis_refuses_trend_claim_without_previous_period() -> None:
    result = analyze_account_metrics(
        _view(account_rows=[("2026-08-01", {"play": 120})]),
        account_id=7,
        days=7,
        comparison="previous_period",
        metric_codes=["play"],
        top_n=5,
        today=date(2026, 8, 5),
    )

    assert result.answerability.status == "partial"
    assert "play:trend" in result.answerability.unsupported_claims
    assert result.facts[0].direction == "unavailable"
    assert result.facts[0].current_value == 120


def test_equal_length_period_comparison_is_deterministic() -> None:
    result = analyze_account_metrics(
        _view(
            account_rows=[
                ("2026-07-25", {"play": 100}),
                ("2026-08-01", {"play": 150}),
            ]
        ),
        account_id=7,
        days=7,
        comparison="previous_period",
        metric_codes=["play"],
        top_n=5,
        today=date(2026, 8, 5),
    )

    fact = result.facts[0]
    assert fact.current_value == 150
    assert fact.previous_value == 100
    assert fact.absolute_change == 50
    assert fact.relative_change == 0.5
    assert fact.direction == "up"
    assert result.answerability.status == "sufficient"


def test_empty_confirmed_view_is_insufficient() -> None:
    result = analyze_account_metrics(
        _view(),
        account_id=7,
        days=30,
        comparison="previous_period",
        metric_codes=["play"],
        top_n=5,
        today=date(2026, 8, 5),
    )

    assert result.answerability.status == "insufficient"
    assert result.answerability.confidence == 0
    assert result.answerability.missing_metrics == ["play"]
    assert result.facts == []


def test_evidence_hash_and_order_are_stable_across_retries() -> None:
    view = _view(
        account_rows=[
            ("2026-08-02", {"follower_delta": 8, "play": 240}),
            ("2026-08-01", {"play": 160}),
        ]
    )
    first = analyze_account_metrics(
        view,
        account_id=7,
        days=7,
        comparison="none",
        metric_codes=["play", "follower_delta"],
        top_n=5,
        today=date(2026, 8, 5),
    )
    second = analyze_account_metrics(
        view,
        account_id=7,
        days=7,
        comparison="none",
        metric_codes=["play", "follower_delta"],
        top_n=5,
        today=date(2026, 8, 5),
    )

    assert first.evidence_refs == second.evidence_refs
    assert [item.metric_code for item in first.evidence_refs] == [
        "follower_delta",
        "play",
        "play",
    ]
    assert all(len(item.content_hash) == 64 for item in first.evidence_refs)


def test_content_rankings_are_scoped_to_the_current_period() -> None:
    result = analyze_account_metrics(
        _view(
            content_rows=[
                ("2026-07-20", "过期作品", {"play": 1}),
                ("2026-08-01", "低播放作品", {"play": 80}),
                ("2026-08-02", "高播放作品", {"play": 360}),
            ]
        ),
        account_id=7,
        days=7,
        comparison="none",
        metric_codes=["play"],
        top_n=2,
        today=date(2026, 8, 5),
    )

    assert [(item.title, item.rank_kind, item.value) for item in result.content_rankings] == [
        ("高播放作品", "top", 360),
        ("低播放作品", "bottom", 80),
    ]


def test_unknown_metric_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported metric: gmv"):
        analyze_account_metrics(
            _view(),
            account_id=7,
            days=30,
            comparison="none",
            metric_codes=["gmv"],
            top_n=5,
            today=date(2026, 8, 5),
        )
