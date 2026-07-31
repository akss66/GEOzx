from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AccountMetricSnapshot,
    AudienceProfileItem,
    AudienceProfileSnapshot,
    BenchmarkSnapshot,
    DataFieldObservation,
    DataImportBatch,
    MetricSnapshot,
    PlatformContentRecord,
)
from app.models.enums import DataSourceKind, ImportBatchStatus, Platform
from app.services.account_data_view import AccountDataViewService
from app.services.data_import.projection import (
    decode_observation_value,
    encode_observation_value,
)
from app.services.data_import.service import (
    RowMatchResolution,
    commit_batch,
    create_manual_preview,
    create_preview,
    resolve_row_match,
)
from tests.test_data_import_templates import (
    DAILY_HEADERS,
    SINGLE_CONTENT_HEADERS,
    WORK_LIST_HEADERS,
    workbook_bytes,
)


@pytest.fixture
async def account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Canonical projection account",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def _preview_and_commit(session, admin, account, filename, payload):
    batch = await create_preview(
        session,
        user=admin,
        account=account,
        filename=filename,
        content=payload,
    )
    return await commit_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=batch.id,
        actor=admin,
    )


def test_observation_json_encoding_preserves_nested_dates():
    encoded = encode_observation_value(
        {
            "period_start": date(2026, 7, 1),
            "nested": [datetime(2026, 7, 31, 8, 30, tzinfo=UTC)],
        }
    )

    assert encoded == {
        "kind": "json",
        "value": {
            "period_start": "2026-07-01",
            "nested": ["2026-07-31T08:30:00+00:00"],
        },
    }
    assert decode_observation_value(encoded) == encoded["value"]


@pytest.mark.asyncio
async def test_overlapping_daily_imports_update_shared_dates_and_keep_other_dates(
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    await _preview_and_commit(
        session,
        admin,
        account,
        "daily-first.xlsx",
        workbook_bytes(
            DAILY_HEADERS,
            [
                ["2026-07-01", 10],
                ["2026-07-02", 20],
                ["2026-07-03", 30],
            ],
        ),
    )
    await _preview_and_commit(
        session,
        admin,
        account,
        "daily-second.xlsx",
        workbook_bytes(
            DAILY_HEADERS,
            [
                ["2026-07-02", 200],
                ["2026-07-03", 0],
                ["2026-07-04", 400],
            ],
        ),
    )

    snapshots = list(
        await session.scalars(
            select(AccountMetricSnapshot)
            .where(AccountMetricSnapshot.account_id == account.id)
            .order_by(AccountMetricSnapshot.stat_date)
        )
    )
    play_observations = list(
        await session.scalars(
            select(DataFieldObservation).where(
                DataFieldObservation.account_id == account.id,
                DataFieldObservation.domain == "account_metrics",
                DataFieldObservation.field_name == "total_play",
            )
        )
    )

    assert [(item.stat_date, item.total_play) for item in snapshots] == [
        (date(2026, 7, 1), 10),
        (date(2026, 7, 2), 200),
        (date(2026, 7, 3), 0),
        (date(2026, 7, 4), 400),
    ]
    assert len(play_observations) == 6
    assert any(item.value == {"kind": "int", "value": 0} for item in play_observations)
    view = await AccountDataViewService(session).load(
        account,
        date(2026, 7, 1),
        date(2026, 7, 4),
    )
    assert [
        (item.stat_date, item.metrics["play"].value)
        for item in reversed(view.account_snapshots)
    ] == [
        (date(2026, 7, 1), 10),
        (date(2026, 7, 2), 200),
        (date(2026, 7, 3), 0),
        (date(2026, 7, 4), 400),
    ]


@pytest.mark.asyncio
async def test_daily_account_export_files_merge_by_date_and_only_replace_present_field(
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    imports = [
        ("主页访问", 12),
        ("总粉丝量", 7994),
        ("取关粉丝", 3),
        ("净增粉丝", -11),
        ("封面点击率", "15.61%"),
        ("作品评论", -2),
        ("作品分享", 5),
        ("作品点赞", -21),
    ]
    for metric_header, value in imports:
        await _preview_and_commit(
            session,
            admin,
            account,
            f"数据表现_{metric_header}数据.xlsx",
            workbook_bytes(
                ["日期", metric_header],
                [["2026-07-30", value]],
            ),
        )

    snapshot = await session.scalar(
        select(AccountMetricSnapshot).where(
            AccountMetricSnapshot.account_id == account.id,
            AccountMetricSnapshot.stat_date == date(2026, 7, 30),
        )
    )
    assert snapshot is not None
    assert snapshot.profile_visit_count == 12
    assert snapshot.follower_count == 7994
    assert snapshot.unfollow_count == 3
    assert snapshot.follower_delta == -11
    assert snapshot.cover_click_rate == pytest.approx(0.1561)
    assert snapshot.comment_count == -2
    assert snapshot.share_count == 5
    assert snapshot.like_count == -21

    await _preview_and_commit(
        session,
        admin,
        account,
        "数据表现_作品点赞数据-更新.xlsx",
        workbook_bytes(
            ["日期", "作品点赞"],
            [["2026-07-30", 18]],
        ),
    )
    await session.refresh(snapshot)
    assert snapshot.like_count == 18
    assert snapshot.comment_count == -2
    assert snapshot.follower_count == 7994

    view = await AccountDataViewService(session).load(
        account,
        date(2026, 7, 30),
        date(2026, 7, 30),
    )
    metrics = view.account_snapshots[0].metrics
    assert metrics["profile_visit_count"].value == 12
    assert metrics["unfollow_count"].value == 3
    assert metrics["cover_click_rate"].value == pytest.approx(0.1561)
    assert metrics["like_count"].value == 18
    assert metrics["comment_count"].value == -2


@pytest.mark.asyncio
async def test_partial_content_reimport_preserves_absent_metrics_and_updates_present_fields(
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    first = await _preview_and_commit(
        session,
        admin,
        account,
        "works.xlsx",
        workbook_bytes(
            WORK_LIST_HEADERS,
            [[
                "作品 A",
                "2026-07-18 14:11:20",
                "1min-视频",
                "公开",
                100,
                0.5,
                0.6,
                0.2,
                0.1,
                8.0,
                6,
                1,
                3,
                2,
                4,
                0,
            ]],
        ),
    )
    content = await session.scalar(
        select(PlatformContentRecord).where(
            PlatformContentRecord.canonical_import_batch_id == first.id
        )
    )
    assert content is not None

    second = await create_preview(
        session,
        user=admin,
        account=account,
        filename="single.xlsx",
        content=workbook_bytes(
            SINGLE_CONTENT_HEADERS[:3],
            [["作品 A", "2026-07-18 14:11:20", 120]],
        ),
    )
    if second.rows[0].candidate_content_ids:
        await resolve_row_match(
            session,
            batch=second,
            row_number=second.rows[0].row_number,
            resolution=RowMatchResolution(
                selected_content_id=content.id,
                resolved_by=admin,
            ),
        )
    await commit_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=second.id,
        actor=admin,
    )

    metrics = list(
        await session.scalars(
            select(MetricSnapshot).where(
                MetricSnapshot.account_id == account.id,
                MetricSnapshot.platform_content_record_id == content.id,
                MetricSnapshot.stat_date == date(2026, 7, 18),
            )
        )
    )
    observations = list(
        await session.scalars(
            select(DataFieldObservation).where(
                DataFieldObservation.account_id == account.id,
                DataFieldObservation.domain == "content_metrics",
                DataFieldObservation.entity_key
                == f"account:{account.id}:content:{content.id}",
            )
        )
    )

    assert len(metrics) == 1
    assert metrics[0].play == 120
    assert metrics[0].like_count == 6
    assert len([item for item in observations if item.field_name == "play"]) == 2
    assert len([item for item in observations if item.field_name == "like_count"]) == 1
    view = await AccountDataViewService(session).load(
        account,
        date(2026, 7, 18),
        date(2026, 7, 18),
    )
    assert view.content_snapshots[0].metrics["play"].value == 120
    assert view.content_snapshots[0].metrics["like_count"].value == 6


@pytest.mark.asyncio
async def test_audience_reimport_replaces_same_dimension_without_duplicate_snapshot(
    session,
    admin,
    account,
):
    first = await create_manual_preview(
        session,
        user=admin,
        account=account,
        payload={
            "data_domain": "audience_dimension",
            "stat_date": "2026-07-31",
            "dimension": "gender",
            "total_audience": 100,
            "audience_items": [
                {"label": "female", "value": "60", "ratio": 0.6},
                {"label": "male", "value": "40", "ratio": 0.4},
            ],
        },
    )
    await commit_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=first.id,
        actor=admin,
    )
    second = await create_manual_preview(
        session,
        user=admin,
        account=account,
        payload={
            "data_domain": "audience_dimension",
            "stat_date": "2026-07-31",
            "dimension": "gender",
            "total_audience": 120,
            "audience_items": [
                {"label": "female", "value": "84", "ratio": 0.7},
                {"label": "male", "value": "36", "ratio": 0.3},
            ],
        },
    )
    await commit_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=second.id,
        actor=admin,
    )

    snapshots = list(
        await session.scalars(
            select(AudienceProfileSnapshot).where(
                AudienceProfileSnapshot.account_id == account.id,
                AudienceProfileSnapshot.stat_date == date(2026, 7, 31),
                AudienceProfileSnapshot.dimension == "gender",
            )
        )
    )
    await session.refresh(snapshots[0], attribute_names=["items"])

    assert len(snapshots) == 1
    assert snapshots[0].total_audience == 120
    assert [(item.label, item.ratio) for item in snapshots[0].items] == [
        ("female", 0.7),
        ("male", 0.3),
    ]


@pytest.mark.asyncio
async def test_benchmark_reimport_updates_value_and_preserves_missing_sample_size(
    session,
    admin,
    account,
):
    first = await create_manual_preview(
        session,
        user=admin,
        account=account,
        payload={
            "data_domain": "benchmark",
            "stat_date": "2026-07-31",
            "benchmark_code": "track_median",
            "benchmark_metrics": [
                {"metric_code": "play", "metric_value": 100, "sample_size": 10},
            ],
        },
    )
    await commit_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=first.id,
        actor=admin,
    )
    second = await create_manual_preview(
        session,
        user=admin,
        account=account,
        payload={
            "data_domain": "benchmark",
            "stat_date": "2026-07-31",
            "benchmark_code": "track_median",
            "benchmark_metrics": [
                {"metric_code": "play", "metric_value": 120, "sample_size": None},
            ],
        },
    )
    await commit_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=second.id,
        actor=admin,
    )

    snapshots = list(
        await session.scalars(
            select(BenchmarkSnapshot).where(
                BenchmarkSnapshot.account_id == account.id,
                BenchmarkSnapshot.stat_date == date(2026, 7, 31),
                BenchmarkSnapshot.benchmark_code == "track_median",
                BenchmarkSnapshot.metric_code == "play",
            )
        )
    )

    assert len(snapshots) == 1
    assert snapshots[0].metric_value == 120
    assert snapshots[0].sample_size == 10


@pytest.mark.asyncio
async def test_view_collapses_legacy_duplicate_account_audience_and_benchmark_rows(
    session,
    admin,
    account,
):
    older = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="legacy_v1",
        content_sha256="a" * 64,
        committed_at=datetime(2026, 7, 30, 8, tzinfo=UTC),
    )
    newer = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="legacy_v1",
        content_sha256="b" * 64,
        committed_at=datetime(2026, 7, 31, 8, tzinfo=UTC),
    )
    session.add_all([older, newer])
    await session.flush()
    session.add_all(
        [
            AccountMetricSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                import_batch_id=older.id,
                source_kind=older.source_kind,
                stat_date=date(2026, 7, 31),
                total_play=10,
            ),
            AccountMetricSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                import_batch_id=newer.id,
                source_kind=newer.source_kind,
                stat_date=date(2026, 7, 31),
                total_play=20,
            ),
            BenchmarkSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                import_batch_id=older.id,
                source_kind=older.source_kind,
                stat_date=date(2026, 7, 31),
                benchmark_code="track",
                metric_code="play",
                metric_value=10,
            ),
            BenchmarkSnapshot(
                org_id=account.org_id,
                account_id=account.id,
                import_batch_id=newer.id,
                source_kind=newer.source_kind,
                stat_date=date(2026, 7, 31),
                benchmark_code="track",
                metric_code="play",
                metric_value=20,
            ),
        ]
    )
    old_audience = AudienceProfileSnapshot(
        org_id=account.org_id,
        account_id=account.id,
        import_batch_id=older.id,
        source_kind=older.source_kind,
        stat_date=date(2026, 7, 31),
        dimension="gender",
        total_audience=10,
    )
    new_audience = AudienceProfileSnapshot(
        org_id=account.org_id,
        account_id=account.id,
        import_batch_id=newer.id,
        source_kind=newer.source_kind,
        stat_date=date(2026, 7, 31),
        dimension="gender",
        total_audience=20,
    )
    session.add_all([old_audience, new_audience])
    await session.flush()
    session.add_all(
        [
            AudienceProfileItem(
                org_id=account.org_id,
                account_id=account.id,
                snapshot_id=old_audience.id,
                label="female",
                value="6",
                ratio=0.6,
                rank=1,
            ),
            AudienceProfileItem(
                org_id=account.org_id,
                account_id=account.id,
                snapshot_id=new_audience.id,
                label="female",
                value="14",
                ratio=0.7,
                rank=1,
            ),
        ]
    )
    await session.commit()

    view = await AccountDataViewService(session).load(
        account,
        date(2026, 7, 31),
        date(2026, 7, 31),
    )

    assert len(view.account_snapshots) == 1
    assert view.account_snapshots[0].metrics["play"].value == 20
    assert len(view.audience) == 1
    assert view.audience[0].total_audience == 20
    assert len(view.benchmarks) == 1
    assert view.benchmarks[0].metric_value == 20
