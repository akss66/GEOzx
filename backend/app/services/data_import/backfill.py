"""Idempotent migration of legacy committed imports into field observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    AccountDataBackfillCheckpoint,
    DataFieldObservation,
    DataImportBatch,
)
from app.models.enums import ImportBatchStatus
from app.services.data_import.projection import ProjectionKey
from app.services.data_import.service import (
    _project_row_targets,
    _rebuild_canonical_projections,
)

CHECKPOINT_NAME = "field_observation_v1"


@dataclass(frozen=True, slots=True)
class AccountDataBackfillResult:
    processed_batches: int
    skipped_batches: int
    completed: bool


async def backfill_account_observations(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    batch_size: int = 100,
) -> AccountDataBackfillResult:
    """Backfill one account in restart-safe chunks and rebuild its projections."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    checkpoint = await session.scalar(
        select(AccountDataBackfillCheckpoint).where(
            AccountDataBackfillCheckpoint.org_id == org_id,
            AccountDataBackfillCheckpoint.account_id == account_id,
            AccountDataBackfillCheckpoint.checkpoint_name == CHECKPOINT_NAME,
        )
    )
    if checkpoint is not None and checkpoint.completed_at is not None:
        return AccountDataBackfillResult(
            processed_batches=0,
            skipped_batches=0,
            completed=True,
        )
    if checkpoint is None:
        checkpoint = AccountDataBackfillCheckpoint(
            org_id=org_id,
            account_id=account_id,
            checkpoint_name=CHECKPOINT_NAME,
        )
        session.add(checkpoint)
        await session.flush()

    processed_batches = 0
    skipped_batches = 0
    while True:
        query = (
            select(DataImportBatch)
            .options(selectinload(DataImportBatch.rows))
            .where(
                DataImportBatch.org_id == org_id,
                DataImportBatch.account_id == account_id,
                DataImportBatch.committed_at.is_not(None),
            )
            .order_by(
                DataImportBatch.committed_at.asc(),
                DataImportBatch.id.asc(),
            )
            .limit(batch_size)
        )
        if checkpoint.last_committed_at is not None:
            query = query.where(
                or_(
                    DataImportBatch.committed_at > checkpoint.last_committed_at,
                    and_(
                        DataImportBatch.committed_at == checkpoint.last_committed_at,
                        DataImportBatch.id > (checkpoint.last_batch_id or 0),
                    ),
                )
            )
        batches = list(await session.scalars(query))
        if not batches:
            checkpoint.completed_at = datetime.now(UTC)
            await session.commit()
            return AccountDataBackfillResult(
                processed_batches=processed_batches,
                skipped_batches=skipped_batches,
                completed=True,
            )

        for batch in batches:
            checkpoint.last_committed_at = batch.committed_at
            checkpoint.last_batch_id = batch.id
            if (
                batch.revoked_at is not None
                or batch.status is ImportBatchStatus.REVOKED
            ):
                skipped_batches += 1
                continue

            if batch.confirmed_sequence is None:
                batch.confirmed_sequence = int(
                    batch.committed_at.timestamp() * 1_000_000
                )
            for row in batch.rows:
                row.projected_target_ids = await _project_row_targets(
                    session=session,
                    batch=batch,
                    row=row,
                )
            observations = list(
                await session.scalars(
                    select(DataFieldObservation).where(
                        DataFieldObservation.org_id == org_id,
                        DataFieldObservation.account_id == account_id,
                        DataFieldObservation.import_batch_id == batch.id,
                        DataFieldObservation.active.is_(True),
                    )
                )
            )
            affected_keys = {
                ProjectionKey(
                    domain=item.domain,
                    entity_key=item.entity_key,
                    stat_date=item.stat_date,
                )
                for item in observations
            }
            if affected_keys:
                await _rebuild_canonical_projections(
                    session=session,
                    batch=batch,
                    affected_keys=affected_keys,
                )
            processed_batches += 1
            checkpoint.processed_batch_count += 1

        await session.commit()
