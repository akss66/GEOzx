from datetime import date

import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AccountMetricSnapshot,
    AudienceProfileSnapshot,
    BenchmarkSnapshot,
    DataFieldObservation,
    MetricSnapshot,
    PlatformContentRecord,
)
from app.models.enums import Platform
from app.services.data_import.service import (
    RowMatchResolution,
    commit_batch,
    create_manual_preview,
    create_preview,
    delete_batch_permanently,
    resolve_row_match,
    revoke_batch,
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
        nickname="Projection rebuild account",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def _commit_daily_play(
    session,
    *,
    admin,
    account,
    filename: str,
    value: int,
):
    batch = await create_preview(
        session,
        user=admin,
        account=account,
        filename=filename,
        content=workbook_bytes(DAILY_HEADERS, [["2026-07-31", value]]),
    )
    return await commit_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=batch.id,
        actor=admin,
    )


@pytest.mark.asyncio
async def test_revoke_newer_batch_restores_older_surviving_account_value(
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    older = await _commit_daily_play(
        session,
        admin=admin,
        account=account,
        filename="older.xlsx",
        value=100,
    )
    newer = await _commit_daily_play(
        session,
        admin=admin,
        account=account,
        filename="newer.xlsx",
        value=200,
    )

    await revoke_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=newer.id,
        actor=admin,
    )

    snapshots = list(
        await session.scalars(
            select(AccountMetricSnapshot).where(
                AccountMetricSnapshot.account_id == account.id,
                AccountMetricSnapshot.stat_date == date(2026, 7, 31),
            )
        )
    )
    observations = list(
        await session.scalars(
            select(DataFieldObservation).where(
                DataFieldObservation.account_id == account.id,
                DataFieldObservation.field_name == "total_play",
            )
        )
    )

    assert len(snapshots) == 1
    assert snapshots[0].total_play == 100
    assert snapshots[0].import_batch_id == older.id
    assert {item.import_batch_id: item.active for item in observations} == {
        older.id: True,
        newer.id: False,
    }


@pytest.mark.asyncio
async def test_permanent_delete_newer_batch_restores_older_surviving_account_value(
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    older = await _commit_daily_play(
        session,
        admin=admin,
        account=account,
        filename="older-delete.xlsx",
        value=300,
    )
    newer = await _commit_daily_play(
        session,
        admin=admin,
        account=account,
        filename="newer-delete.xlsx",
        value=400,
    )

    await delete_batch_permanently(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=newer.id,
        actor=admin,
    )

    snapshot = await session.scalar(
        select(AccountMetricSnapshot).where(
            AccountMetricSnapshot.account_id == account.id,
            AccountMetricSnapshot.stat_date == date(2026, 7, 31),
        )
    )
    observations = list(
        await session.scalars(
            select(DataFieldObservation).where(
                DataFieldObservation.account_id == account.id,
                DataFieldObservation.field_name == "total_play",
            )
        )
    )

    assert snapshot is not None
    assert snapshot.total_play == 300
    assert snapshot.import_batch_id == older.id
    assert [(item.import_batch_id, item.active) for item in observations] == [
        (older.id, True)
    ]


@pytest.mark.asyncio
async def test_revoke_newer_content_batch_restores_older_metrics(
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    older = await create_preview(
        session,
        user=admin,
        account=account,
        filename="older-content.xlsx",
        content=workbook_bytes(
            WORK_LIST_HEADERS,
            [[
                "作品 A",
                "2026-07-18 14:11:20",
                "1min-video",
                "public",
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
    older = await commit_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=older.id,
        actor=admin,
    )
    content_record = await session.scalar(
        select(PlatformContentRecord).where(
            PlatformContentRecord.canonical_import_batch_id == older.id
        )
    )
    newer = await create_preview(
        session,
        user=admin,
        account=account,
        filename="newer-content.xlsx",
        content=workbook_bytes(
            SINGLE_CONTENT_HEADERS[:3],
            [["作品 A", "2026-07-18 14:11:20", 200]],
        ),
    )
    await resolve_row_match(
        session,
        batch=newer,
        row_number=newer.rows[0].row_number,
        resolution=RowMatchResolution(
            selected_content_id=content_record.id,
            resolved_by=admin,
        ),
    )
    newer = await commit_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=newer.id,
        actor=admin,
    )

    await revoke_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=newer.id,
        actor=admin,
    )

    snapshot = await session.scalar(
        select(MetricSnapshot).where(
            MetricSnapshot.account_id == account.id,
            MetricSnapshot.platform_content_record_id == content_record.id,
            MetricSnapshot.stat_date == date(2026, 7, 18),
        )
    )
    assert snapshot is not None
    assert snapshot.play == 100
    assert snapshot.like_count == 6
    assert snapshot.import_batch_id == older.id

    surviving = await create_preview(
        session,
        user=admin,
        account=account,
        filename="surviving-content.xlsx",
        content=workbook_bytes(
            SINGLE_CONTENT_HEADERS[:3],
            [["作品 A", "2026-07-18 14:11:20", 300]],
        ),
    )
    await resolve_row_match(
        session,
        batch=surviving,
        row_number=surviving.rows[0].row_number,
        resolution=RowMatchResolution(
            selected_content_id=content_record.id,
            resolved_by=admin,
        ),
    )
    surviving = await commit_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=surviving.id,
        actor=admin,
    )
    await delete_batch_permanently(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=older.id,
        actor=admin,
    )
    await session.refresh(content_record)
    await session.refresh(snapshot)

    assert content_record.canonical_import_batch_id == surviving.id
    assert snapshot.play == 300
    assert snapshot.import_batch_id == surviving.id


@pytest.mark.asyncio
async def test_revoke_newer_audience_and_benchmark_restores_older_values(
    session,
    admin,
    account,
):
    async def commit_manual(payload):
        batch = await create_manual_preview(
            session,
            user=admin,
            account=account,
            payload=payload,
        )
        return await commit_batch(
            session,
            org_id=account.org_id,
            account_id=account.id,
            batch_id=batch.id,
            actor=admin,
        )

    older_audience = await commit_manual(
        {
            "data_domain": "audience_dimension",
            "stat_date": "2026-07-31",
            "dimension": "gender",
            "total_audience": 100,
            "audience_items": [{"label": "female", "value": "60", "ratio": 0.6}],
        }
    )
    newer_audience = await commit_manual(
        {
            "data_domain": "audience_dimension",
            "stat_date": "2026-07-31",
            "dimension": "gender",
            "total_audience": 200,
            "audience_items": [{"label": "female", "value": "140", "ratio": 0.7}],
        }
    )
    older_benchmark = await commit_manual(
        {
            "data_domain": "benchmark",
            "stat_date": "2026-07-31",
            "benchmark_code": "track",
            "benchmark_metrics": [
                {"metric_code": "play", "metric_value": 100, "sample_size": 10}
            ],
        }
    )
    newer_benchmark = await commit_manual(
        {
            "data_domain": "benchmark",
            "stat_date": "2026-07-31",
            "benchmark_code": "track",
            "benchmark_metrics": [
                {"metric_code": "play", "metric_value": 200, "sample_size": 20}
            ],
        }
    )

    for batch in (newer_audience, newer_benchmark):
        await revoke_batch(
            session,
            org_id=account.org_id,
            account_id=account.id,
            batch_id=batch.id,
            actor=admin,
        )

    audience = await session.scalar(
        select(AudienceProfileSnapshot).where(
            AudienceProfileSnapshot.account_id == account.id,
            AudienceProfileSnapshot.stat_date == date(2026, 7, 31),
            AudienceProfileSnapshot.dimension == "gender",
        )
    )
    benchmark = await session.scalar(
        select(BenchmarkSnapshot).where(
            BenchmarkSnapshot.account_id == account.id,
            BenchmarkSnapshot.stat_date == date(2026, 7, 31),
            BenchmarkSnapshot.benchmark_code == "track",
            BenchmarkSnapshot.metric_code == "play",
        )
    )
    assert audience is not None
    assert audience.total_audience == 100
    assert audience.import_batch_id == older_audience.id
    assert benchmark is not None
    assert benchmark.metric_value == 100
    assert benchmark.sample_size == 10
    assert benchmark.import_batch_id == older_benchmark.id


@pytest.mark.asyncio
async def test_delete_newer_audience_and_benchmark_restores_older_values(
    session,
    admin,
    account,
):
    async def commit_manual(payload):
        batch = await create_manual_preview(
            session,
            user=admin,
            account=account,
            payload=payload,
        )
        return await commit_batch(
            session,
            org_id=account.org_id,
            account_id=account.id,
            batch_id=batch.id,
            actor=admin,
        )

    older_audience = await commit_manual(
        {
            "data_domain": "audience_dimension",
            "stat_date": "2026-07-31",
            "dimension": "city",
            "total_audience": 80,
            "audience_items": [{"label": "Shanghai", "value": "40", "ratio": 0.5}],
        }
    )
    newer_audience = await commit_manual(
        {
            "data_domain": "audience_dimension",
            "stat_date": "2026-07-31",
            "dimension": "city",
            "total_audience": 160,
            "audience_items": [{"label": "Shanghai", "value": "96", "ratio": 0.6}],
        }
    )
    older_benchmark = await commit_manual(
        {
            "data_domain": "benchmark",
            "stat_date": "2026-07-31",
            "benchmark_code": "delete-track",
            "benchmark_metrics": [
                {"metric_code": "play", "metric_value": 80, "sample_size": 8}
            ],
        }
    )
    newer_benchmark = await commit_manual(
        {
            "data_domain": "benchmark",
            "stat_date": "2026-07-31",
            "benchmark_code": "delete-track",
            "benchmark_metrics": [
                {"metric_code": "play", "metric_value": 160, "sample_size": 16}
            ],
        }
    )

    for batch in (newer_audience, newer_benchmark):
        await delete_batch_permanently(
            session,
            org_id=account.org_id,
            account_id=account.id,
            batch_id=batch.id,
            actor=admin,
        )

    audience = await session.scalar(
        select(AudienceProfileSnapshot).where(
            AudienceProfileSnapshot.account_id == account.id,
            AudienceProfileSnapshot.stat_date == date(2026, 7, 31),
            AudienceProfileSnapshot.dimension == "city",
        )
    )
    benchmark = await session.scalar(
        select(BenchmarkSnapshot).where(
            BenchmarkSnapshot.account_id == account.id,
            BenchmarkSnapshot.stat_date == date(2026, 7, 31),
            BenchmarkSnapshot.benchmark_code == "delete-track",
            BenchmarkSnapshot.metric_code == "play",
        )
    )
    assert audience is not None
    assert audience.total_audience == 80
    assert audience.import_batch_id == older_audience.id
    assert benchmark is not None
    assert benchmark.metric_value == 80
    assert benchmark.sample_size == 8
    assert benchmark.import_batch_id == older_benchmark.id


@pytest.mark.asyncio
async def test_revoke_is_account_scoped(
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    other = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Other projection account",
    )
    session.add(other)
    await session.commit()
    await session.refresh(other)
    removed = await _commit_daily_play(
        session,
        admin=admin,
        account=account,
        filename="scoped-first.xlsx",
        value=10,
    )
    await _commit_daily_play(
        session,
        admin=admin,
        account=other,
        filename="scoped-other.xlsx",
        value=999,
    )

    await revoke_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=removed.id,
        actor=admin,
    )

    other_snapshot = await session.scalar(
        select(AccountMetricSnapshot).where(
            AccountMetricSnapshot.account_id == other.id,
            AccountMetricSnapshot.stat_date == date(2026, 7, 31),
        )
    )
    assert other_snapshot is not None
    assert other_snapshot.total_play == 999

    await delete_batch_permanently(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=removed.id,
        actor=admin,
    )
    await session.refresh(other_snapshot)
    assert other_snapshot.total_play == 999
