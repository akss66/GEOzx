from datetime import date

import pytest
from sqlalchemy import delete, func, select

from app.models import (
    Account,
    AccountDataBackfillCheckpoint,
    AccountMetricSnapshot,
    DataFieldObservation,
)
from app.models.enums import Platform
from app.services.data_import.backfill import backfill_account_observations
from app.services.data_import.service import (
    commit_batch,
    create_manual_preview,
    revoke_batch,
)


@pytest.fixture
async def account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Legacy backfill account",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def _commit_account_metrics(
    session,
    *,
    admin,
    account,
    follower_count: int | None,
    total_play: int | None,
):
    batch = await create_manual_preview(
        session,
        user=admin,
        account=account,
        payload={
            "data_domain": "account_period_totals",
            "stat_date": "2026-07-31",
            "period_start": "2026-07-01",
            "period_end": "2026-07-31",
            "account_metrics": {
                "follower_count": follower_count,
                "follower_delta": None,
                "total_play": total_play,
                "total_exposure": None,
                "engagement_rate": None,
            },
        },
    )
    return await commit_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=batch.id,
        actor=admin,
    )


@pytest.mark.asyncio
async def test_backfill_is_idempotent_deduplicates_legacy_snapshots_and_preserves_zero(
    session,
    admin,
    account,
):
    older = await _commit_account_metrics(
        session,
        admin=admin,
        account=account,
        follower_count=1000,
        total_play=100,
    )
    newer = await _commit_account_metrics(
        session,
        admin=admin,
        account=account,
        follower_count=None,
        total_play=0,
    )
    revoked = await _commit_account_metrics(
        session,
        admin=admin,
        account=account,
        follower_count=9999,
        total_play=9999,
    )
    await revoke_batch(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_id=revoked.id,
        actor=admin,
    )

    # Simulate the pre-observation production state and one duplicate legacy projection.
    await session.execute(
        delete(DataFieldObservation).where(
            DataFieldObservation.account_id == account.id,
        )
    )
    older.confirmed_sequence = None
    newer.confirmed_sequence = None
    revoked.confirmed_sequence = None
    session.add(
        AccountMetricSnapshot(
            org_id=account.org_id,
            account_id=account.id,
            import_batch_id=older.id,
            source_kind=older.source_kind,
            stat_date=date(2026, 7, 31),
            follower_count=1000,
            total_play=100,
        )
    )
    await session.commit()

    first = await backfill_account_observations(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_size=1,
    )
    observation_count = await session.scalar(
        select(func.count(DataFieldObservation.id)).where(
            DataFieldObservation.account_id == account.id,
        )
    )
    second = await backfill_account_observations(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_size=1,
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
            )
        )
    )

    assert first.processed_batches == 2
    assert first.skipped_batches == 1
    assert second.processed_batches == 0
    assert await session.scalar(
        select(func.count(DataFieldObservation.id)).where(
            DataFieldObservation.account_id == account.id,
        )
    ) == observation_count
    assert {item.import_batch_id for item in observations} == {older.id, newer.id}
    assert len(snapshots) == 1
    assert snapshots[0].follower_count == 1000
    assert snapshots[0].total_play == 0
    assert snapshots[0].import_batch_id == newer.id


@pytest.mark.asyncio
async def test_backfill_records_a_resumable_account_checkpoint(
    session,
    admin,
    account,
):
    batch = await _commit_account_metrics(
        session,
        admin=admin,
        account=account,
        follower_count=800,
        total_play=88,
    )
    await session.execute(
        delete(DataFieldObservation).where(
            DataFieldObservation.account_id == account.id,
        )
    )
    batch.confirmed_sequence = None
    await session.commit()

    result = await backfill_account_observations(
        session,
        org_id=account.org_id,
        account_id=account.id,
        batch_size=10,
    )
    checkpoint = await session.scalar(
        select(AccountDataBackfillCheckpoint).where(
            AccountDataBackfillCheckpoint.org_id == account.org_id,
            AccountDataBackfillCheckpoint.account_id == account.id,
        )
    )

    assert result.completed is True
    assert checkpoint is not None
    assert checkpoint.last_batch_id == batch.id
    assert checkpoint.last_committed_at.replace(tzinfo=None) == batch.committed_at.replace(
        tzinfo=None
    )
    assert checkpoint.completed_at is not None
