from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import event

from app.models import (
    Account,
    AccountMetricSnapshot,
    AudienceProfileItem,
    AudienceProfileSnapshot,
    BenchmarkSnapshot,
    DataConflict,
    DataImportBatch,
    DataImportRow,
    MetricSnapshot,
    PlatformContentRecord,
)
from app.models.enums import (
    ConflictStatus,
    ContentIdentityConfidence,
    DataSourceKind,
    ImportBatchStatus,
    ImportRowStatus,
    MetricSource,
    Platform,
)
from app.services.account_data_view import AccountDataViewService


@pytest.fixture
async def account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Unified account data view",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_official_value_wins_without_destroying_export_evidence(session, admin, account):
    start = date(2026, 7, 18)
    end = date(2026, 7, 22)
    batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_work_list_v1",
        content_sha256="1" * 64,
        period_start=start,
        period_end=end,
        committed_at=datetime(2026, 7, 22, 9, 0, tzinfo=UTC),
    )
    session.add(batch)
    await session.flush()
    content = PlatformContentRecord(
        org_id=account.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        canonical_import_batch_id=batch.id,
        canonical_import_row_number=2,
        title="Explainer clip",
        published_at=datetime(2026, 7, 20, 12, 30),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(content)
    await session.flush()

    imported = MetricSnapshot(
        org_id=account.org_id,
        account_id=account.id,
        import_batch_id=batch.id,
        platform_content_record_id=content.id,
        source=MetricSource.DOUYIN,
        stat_date=date(2026, 7, 20),
        title="Explainer clip",
        play=100,
        exposure=0,
        completion_rate=0.0,
        like_rate=0.0,
        comment_rate=0.0,
        share_rate=0.0,
        follower_delta=0,
    )
    session.add(imported)
    await session.flush()
    session.add(
        DataImportRow(
            org_id=account.org_id,
            account_id=account.id,
            batch_id=batch.id,
            row_number=2,
            status=ImportRowStatus.COMMITTED,
            normalized_values={
                "title": "Explainer clip",
                "published_at": "2026-07-20T12:30:00",
                "play": 100,
            },
            projected_target_ids=[
                {"kind": "platform_content_record", "id": content.id, "action": "linked"},
                {"kind": "metric_snapshot", "id": imported.id, "action": "created"},
                {
                    "kind": "projection_gap",
                    "missing_fields": ["exposure", "cover_click_rate"],
                    "report": "staging_only",
                },
            ],
            platform_content_record_id=content.id,
        )
    )
    session.add(
        MetricSnapshot(
            org_id=account.org_id,
            account_id=account.id,
            platform_content_record_id=content.id,
            source=MetricSource.DOUYIN,
            stat_date=date(2026, 7, 20),
            title="Explainer clip",
            play=120,
            exposure=300,
            completion_rate=0.31,
            like_rate=0.08,
            comment_rate=0.02,
            share_rate=0.01,
            follower_delta=5,
        )
    )
    await session.commit()

    view = await AccountDataViewService(session).load(account, start, end)

    metric = view.content_snapshots[0].metrics["play"]
    assert metric.value == 120
    assert metric.source == DataSourceKind.OFFICIAL_API
    assert {item.value for item in metric.observations} == {100, 120}


@pytest.mark.asyncio
async def test_missing_metric_remains_none(session, admin, account):
    start = date(2026, 7, 18)
    end = date(2026, 7, 22)
    batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_work_list_v1",
        content_sha256="2" * 64,
        period_start=start,
        period_end=end,
        committed_at=datetime(2026, 7, 22, 10, 0, tzinfo=UTC),
    )
    session.add(batch)
    await session.flush()
    content = PlatformContentRecord(
        org_id=account.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        canonical_import_batch_id=batch.id,
        canonical_import_row_number=2,
        title="Coverage gap clip",
        published_at=datetime(2026, 7, 19, 10, 0),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(content)
    await session.flush()

    imported = MetricSnapshot(
        org_id=account.org_id,
        account_id=account.id,
        import_batch_id=batch.id,
        platform_content_record_id=content.id,
        source=MetricSource.DOUYIN,
        stat_date=date(2026, 7, 19),
        title="Coverage gap clip",
        play=88,
        exposure=0,
        completion_rate=0.0,
        like_rate=0.0,
        comment_rate=0.0,
        share_rate=0.0,
        follower_delta=0,
    )
    session.add(imported)
    await session.flush()
    session.add(
        DataImportRow(
            org_id=account.org_id,
            account_id=account.id,
            batch_id=batch.id,
            row_number=2,
            status=ImportRowStatus.COMMITTED,
            normalized_values={
                "title": "Coverage gap clip",
                "published_at": "2026-07-19T10:00:00",
                "play": 88,
            },
            projected_target_ids=[
                {"kind": "metric_snapshot", "id": imported.id, "action": "created"},
                {
                    "kind": "projection_gap",
                    "missing_fields": ["cover_click_rate"],
                    "report": "staging_only",
                },
            ],
            platform_content_record_id=content.id,
        )
    )
    await session.commit()

    view = await AccountDataViewService(session).load(account, start, end)

    assert view.content_snapshots[0].metrics["cover_click_rate"].value is None


@pytest.mark.asyncio
async def test_load_returns_account_audience_benchmark_coverage_and_conflicts(
    session, admin, account
):
    start = date(2026, 7, 18)
    end = date(2026, 7, 22)
    account_batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_daily_play_v1",
        content_sha256="3" * 64,
        period_start=start,
        period_end=end,
        committed_at=datetime(2026, 7, 22, 8, 0, tzinfo=UTC),
    )
    audience_batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.MANUAL_ENTRY,
        status=ImportBatchStatus.COMMITTED,
        template_code="audience_manual_v1",
        content_sha256="4" * 64,
        period_start=start,
        period_end=end,
        committed_at=datetime(2026, 7, 22, 11, 0, tzinfo=UTC),
    )
    benchmark_batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.SCREENSHOT_VERIFIED,
        status=ImportBatchStatus.COMMITTED,
        template_code="benchmark_manual_v1",
        content_sha256="5" * 64,
        period_start=start,
        period_end=end,
        committed_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
    )
    session.add_all([account_batch, audience_batch, benchmark_batch])
    await session.flush()

    audience_snapshot = AudienceProfileSnapshot(
        org_id=account.org_id,
        account_id=account.id,
        import_batch_id=audience_batch.id,
        source_kind=DataSourceKind.MANUAL_ENTRY,
        stat_date=date(2026, 7, 21),
        dimension="gender",
        total_audience=1000,
    )
    session.add_all(
        [
            AccountMetricSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                import_batch_id=account_batch.id,
                source_kind=DataSourceKind.PLATFORM_EXPORT,
                stat_date=date(2026, 7, 21),
                total_play=81,
            ),
            audience_snapshot,
            BenchmarkSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                import_batch_id=benchmark_batch.id,
                source_kind=DataSourceKind.SCREENSHOT_VERIFIED,
                stat_date=date(2026, 7, 21),
                benchmark_code="peer:tech",
                metric_code="median_play",
                metric_value=456.0,
            ),
        ]
    )
    await session.flush()
    session.add_all(
        [
            AudienceProfileItem(
                org_id=account.org_id,
                account_id=account.id,
                snapshot_id=audience_snapshot.id,
                label="female",
                value="0.63",
                ratio=0.63,
                rank=1,
            ),
            DataConflict(
                org_id=account.org_id,
                account_id=account.id,
                batch_id=audience_batch.id,
                row_number=2,
                status=ConflictStatus.OPEN,
                field_name="audience",
                conflict_code="manual_review",
                message="Needs confirmation",
            ),
        ]
    )
    await session.commit()

    view = await AccountDataViewService(session).load(account, start, end)

    assert view.coverage == {
        "account_metrics": "available",
        "content_metrics": "missing",
        "content_identity": "missing",
        "audience": "available",
        "benchmarks": "available",
    }
    assert view.latest_confirmed_at == datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    assert len(view.account_snapshots) == 1
    assert view.account_snapshots[0].metrics["play"].value == 81
    assert len(view.audience) == 1
    assert view.audience[0].items[0].label == "female"
    assert len(view.benchmarks) == 1
    assert len(view.conflicts) == 1


@pytest.mark.asyncio
async def test_same_title_same_day_rows_without_stable_identity_stay_separate(
    session, admin, account
):
    start = date(2026, 7, 18)
    end = date(2026, 7, 22)
    session.add_all(
        [
            MetricSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                source=MetricSource.MANUAL,
                stat_date=date(2026, 7, 20),
                title="Duplicate title",
                play=100,
                exposure=200,
                completion_rate=0.2,
                like_rate=0.05,
                comment_rate=0.01,
                share_rate=0.01,
                follower_delta=1,
            ),
            MetricSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                source=MetricSource.MANUAL,
                stat_date=date(2026, 7, 20),
                title="Duplicate title",
                play=300,
                exposure=400,
                completion_rate=0.4,
                like_rate=0.08,
                comment_rate=0.02,
                share_rate=0.01,
                follower_delta=2,
            ),
        ]
    )
    await session.commit()

    view = await AccountDataViewService(session).load(account, start, end)

    assert len(view.content_snapshots) == 2
    assert sorted(item.metrics["play"].value for item in view.content_snapshots) == [100, 300]
    assert view.coverage["content_identity"] == "ambiguous"


@pytest.mark.asyncio
async def test_non_batch_source_summary_uses_observed_period_and_sync_time(session, admin, account):
    start = date(2026, 7, 1)
    end = date(2026, 7, 31)
    first = MetricSnapshot(
        org_id=account.org_id,
        account_id=account.id,
        source=MetricSource.MANUAL,
        stat_date=date(2026, 7, 18),
        title="Manual evidence A",
        play=10,
        exposure=20,
        completion_rate=0.2,
        like_rate=0.05,
        comment_rate=0.01,
        share_rate=0.01,
        follower_delta=1,
    )
    second = MetricSnapshot(
        org_id=account.org_id,
        account_id=account.id,
        source=MetricSource.MANUAL,
        stat_date=date(2026, 7, 20),
        title="Manual evidence B",
        play=30,
        exposure=40,
        completion_rate=0.3,
        like_rate=0.06,
        comment_rate=0.01,
        share_rate=0.02,
        follower_delta=2,
    )
    session.add_all([first, second])
    await session.flush()
    first.updated_at = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
    second.updated_at = datetime(2026, 7, 21, 9, 30, tzinfo=UTC)
    await session.commit()

    view = await AccountDataViewService(session).load(account, start, end)

    summary = next(item for item in view.source_summary if item.source_kind == "legacy_manual")
    assert summary.period_start == date(2026, 7, 18)
    assert summary.period_end == date(2026, 7, 20)
    assert view.latest_synced_at == datetime(2026, 7, 21, 9, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_load_prefilters_relevant_batches_and_ignores_out_of_window_batch_conflicts(
    session, admin, account
):
    start = date(2026, 7, 18)
    end = date(2026, 7, 22)
    relevant_batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_daily_play_v1",
        content_sha256="8" * 64,
        period_start=start,
        period_end=end,
        committed_at=datetime(2026, 7, 22, 8, 0, tzinfo=UTC),
    )
    out_of_window_batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_daily_play_v1",
        content_sha256="9" * 64,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 2),
        committed_at=datetime(2026, 6, 2, 8, 0, tzinfo=UTC),
    )
    session.add_all([relevant_batch, out_of_window_batch])
    await session.flush()
    session.add_all(
        [
            AccountMetricSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                import_batch_id=relevant_batch.id,
                source_kind=DataSourceKind.PLATFORM_EXPORT,
                stat_date=date(2026, 7, 21),
                total_play=81,
            ),
            AccountMetricSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                import_batch_id=out_of_window_batch.id,
                source_kind=DataSourceKind.PLATFORM_EXPORT,
                stat_date=date(2026, 6, 2),
                total_play=999,
            ),
            DataImportRow(
                org_id=account.org_id,
                account_id=account.id,
                batch_id=out_of_window_batch.id,
                row_number=2,
                status=ImportRowStatus.COMMITTED,
                normalized_values={"play": 999, "stat_date": "2026-06-02"},
                projected_target_ids=[],
            ),
            DataConflict(
                org_id=account.org_id,
                account_id=account.id,
                batch_id=out_of_window_batch.id,
                row_number=2,
                status=ConflictStatus.OPEN,
                field_name="play",
                conflict_code="stale_conflict",
                message="Should stay outside the requested window",
            ),
        ]
    )
    await session.commit()

    statements: list[str] = []
    sync_engine = session.get_bind()

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    try:
        view = await AccountDataViewService(session).load(account, start, end)
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)

    assert [item.batch_id for item in view.source_summary] == [relevant_batch.id]
    assert view.conflicts == []
    assert any(
        "FROM data_import_batches" in statement and "data_import_batches.id IN" in statement
        for statement in statements
    )
