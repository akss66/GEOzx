"""Fail-closed DB-only freshness adapter boundary for checkpoint reuse."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import Any, Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    AccountMetricSnapshot,
    AudienceProfileItem,
    AudienceProfileSnapshot,
    BenchmarkSnapshot,
    ContentItem,
    DataArtifact,
    DataConflict,
    DataFieldObservation,
    DataImportBatch,
    Deliverable,
    MetricSnapshot,
    PlatformContentRecord,
)
from app.models.enums import MetricSource
from app.orchestrator.checkpoint_graph_contracts import CheckpointStepSpec
from app.orchestrator.runtime_scope import RuntimeScope
from app.schemas.run_revision import FreshnessStamp, StageDataEnvelope


class FreshnessValidator(Protocol):
    key: str

    async def current_stamp(
        self,
        session: AsyncSession,
        *,
        scope: RuntimeScope,
        step: CheckpointStepSpec,
        input: StageDataEnvelope,
        db_now: datetime,
    ) -> FreshnessStamp: ...


@dataclass(frozen=True)
class FreshnessVerdict:
    kind: Literal["reusable", "full_recompute"]
    reason: str | None
    validated_at: datetime | None = None
    stamp: FreshnessStamp | None = None


class _AccountDatabaseFreshnessValidator:
    """A bounded watermark over authoritative account-owned database rows."""

    def __init__(self, key: str, *, ttl: timedelta) -> None:
        self.key = key
        self._ttl = ttl

    async def current_stamp(
        self,
        session: AsyncSession,
        *,
        scope: RuntimeScope,
        step: CheckpointStepSpec,
        input: StageDataEnvelope,
        db_now: datetime,
    ) -> FreshnessStamp:
        from app.services.checkpoint_hashing import canonical_json_sha256

        account = (
            await session.execute(
                select(
                    Account.id,
                    Account.org_id,
                    Account.platform,
                    Account.nickname,
                    Account.status,
                    Account.updated_at,
                ).where(Account.id == scope.account_id)
            )
        ).one_or_none()
        if account is None or account.org_id != scope.org_id:
            raise RuntimeError("freshness account scope does not exist")
        content_rows = tuple(
            (
                await session.execute(
                    select(ContentItem.id, ContentItem.updated_at)
                    .where(ContentItem.account_id == scope.account_id)
                    .order_by(ContentItem.id)
                )
            ).all()
        )
        deliverable_rows = tuple(
            (
                await session.execute(
                    select(Deliverable.id, Deliverable.version, Deliverable.updated_at)
                    .join(ContentItem, ContentItem.id == Deliverable.content_item_id)
                    .where(ContentItem.account_id == scope.account_id)
                    .order_by(Deliverable.id)
                )
            ).all()
        )
        batch_rows = tuple(
            (
                await session.execute(
                    select(
                        DataImportBatch.id,
                        DataImportBatch.source_kind,
                        DataImportBatch.status,
                        DataImportBatch.template_code,
                        DataImportBatch.content_sha256,
                        DataImportBatch.parser_version,
                        DataImportBatch.sheet_name,
                        DataImportBatch.dataset_ordinal,
                        DataImportBatch.confirmed_sequence,
                        DataImportBatch.period_start,
                        DataImportBatch.period_end,
                        DataImportBatch.row_count,
                        DataImportBatch.committed_at,
                        DataImportBatch.revoked_at,
                        DataImportBatch.updated_at,
                    )
                    .where(
                        DataImportBatch.org_id == scope.org_id,
                        DataImportBatch.account_id == scope.account_id,
                    )
                    .order_by(DataImportBatch.id)
                )
            ).all()
        )
        artifact_rows = tuple(
            (
                await session.execute(
                    select(
                        DataArtifact.id,
                        DataArtifact.batch_id,
                        DataArtifact.filename,
                        DataArtifact.content_type,
                        DataArtifact.byte_size,
                        DataArtifact.sha256,
                        DataArtifact.storage_key,
                        DataArtifact.updated_at,
                    )
                    .where(
                        DataArtifact.org_id == scope.org_id,
                        DataArtifact.account_id == scope.account_id,
                    )
                    .order_by(DataArtifact.id)
                )
            ).all()
        )
        observation_rows = tuple(
            (
                await session.execute(
                    select(
                        DataFieldObservation.id,
                        DataFieldObservation.import_batch_id,
                        DataFieldObservation.import_row_id,
                        DataFieldObservation.domain,
                        DataFieldObservation.entity_key,
                        DataFieldObservation.stat_date,
                        DataFieldObservation.field_name,
                        DataFieldObservation.value,
                        DataFieldObservation.source_kind,
                        DataFieldObservation.source_priority,
                        DataFieldObservation.confirmed_sequence,
                        DataFieldObservation.active,
                        DataFieldObservation.updated_at,
                    )
                    .where(
                        DataFieldObservation.org_id == scope.org_id,
                        DataFieldObservation.account_id == scope.account_id,
                    )
                    .order_by(DataFieldObservation.id)
                )
            ).all()
        )
        conflict_rows = tuple(
            (
                await session.execute(
                    select(
                        DataConflict.id,
                        DataConflict.batch_id,
                        DataConflict.row_number,
                        DataConflict.status,
                        DataConflict.field_name,
                        DataConflict.conflict_code,
                        DataConflict.existing_value,
                        DataConflict.incoming_value,
                        DataConflict.candidate_content_ids,
                        DataConflict.resolved_by_id,
                        DataConflict.resolved_at,
                        DataConflict.updated_at,
                    )
                    .where(
                        DataConflict.org_id == scope.org_id,
                        DataConflict.account_id == scope.account_id,
                    )
                    .order_by(DataConflict.id)
                )
            ).all()
        )
        period_start, period_end = _query_window(input)
        metric_statement = select(MetricSnapshot).where(
            MetricSnapshot.org_id == scope.org_id,
            MetricSnapshot.account_id == scope.account_id,
            MetricSnapshot.source != MetricSource.DEMO,
        )
        account_snapshot_statement = select(AccountMetricSnapshot).where(
            AccountMetricSnapshot.org_id == scope.org_id,
            AccountMetricSnapshot.account_id == scope.account_id,
        )
        audience_statement = select(AudienceProfileSnapshot).where(
            AudienceProfileSnapshot.org_id == scope.org_id,
            AudienceProfileSnapshot.account_id == scope.account_id,
        )
        benchmark_statement = select(BenchmarkSnapshot).where(
            BenchmarkSnapshot.org_id == scope.org_id,
            BenchmarkSnapshot.account_id == scope.account_id,
        )
        if period_start is not None and period_end is not None:
            metric_statement = metric_statement.where(
                MetricSnapshot.stat_date.between(period_start, period_end)
            )
            account_snapshot_statement = account_snapshot_statement.where(
                AccountMetricSnapshot.stat_date.between(period_start, period_end)
            )
            audience_statement = audience_statement.where(
                AudienceProfileSnapshot.stat_date.between(period_start, period_end)
            )
            benchmark_statement = benchmark_statement.where(
                BenchmarkSnapshot.stat_date.between(period_start, period_end)
            )
        metric_snapshots = tuple(
            (
                await session.scalars(
                    metric_statement.order_by(MetricSnapshot.id)
                )
            ).all()
        )
        account_snapshots = tuple(
            (
                await session.scalars(
                    account_snapshot_statement.order_by(AccountMetricSnapshot.id)
                )
            ).all()
        )
        audience_snapshots = tuple(
            (
                await session.scalars(
                    audience_statement.order_by(AudienceProfileSnapshot.id)
                )
            ).all()
        )
        audience_ids = tuple(row.id for row in audience_snapshots)
        audience_items = (
            tuple(
                (
                    await session.scalars(
                        select(AudienceProfileItem)
                        .where(AudienceProfileItem.snapshot_id.in_(audience_ids))
                        .order_by(AudienceProfileItem.id)
                    )
                ).all()
            )
            if audience_ids
            else ()
        )
        benchmark_snapshots = tuple(
            (
                await session.scalars(
                    benchmark_statement.order_by(BenchmarkSnapshot.id)
                )
            ).all()
        )
        platform_content_ids = tuple(
            sorted(
                {
                    row.platform_content_record_id
                    for row in metric_snapshots
                    if row.platform_content_record_id is not None
                }
            )
        )
        platform_contents = (
            tuple(
                (
                    await session.scalars(
                        select(PlatformContentRecord)
                        .where(
                            PlatformContentRecord.org_id == scope.org_id,
                            PlatformContentRecord.account_id == scope.account_id,
                            PlatformContentRecord.id.in_(platform_content_ids),
                        )
                        .order_by(PlatformContentRecord.id)
                    )
                ).all()
            )
            if platform_content_ids
            else ()
        )
        watermark = canonical_json_sha256(
            domain="checkpoint-freshness-watermark/v1",
            value={
                "policy_key": self.key,
                "account": {
                    "id": account.id,
                    "platform": account.platform.value,
                    "nickname": account.nickname,
                    "status": account.status.value,
                    "updated_at": _as_utc(account.updated_at),
                },
                "content_rows": [
                    {"id": row.id, "updated_at": _as_utc(row.updated_at)} for row in content_rows
                ],
                "deliverable_rows": [
                    {
                        "id": row.id,
                        "version": row.version,
                        "updated_at": _as_utc(row.updated_at),
                    }
                    for row in deliverable_rows
                ],
                "import_batches": [
                    {
                        "id": row.id,
                        "source_kind": row.source_kind.value,
                        "status": row.status.value,
                        "template_code": row.template_code,
                        "content_sha256": row.content_sha256,
                        "parser_version": row.parser_version,
                        "sheet_name": row.sheet_name,
                        "dataset_ordinal": row.dataset_ordinal,
                        "confirmed_sequence": row.confirmed_sequence,
                        "period_start": row.period_start,
                        "period_end": row.period_end,
                        "row_count": row.row_count,
                        "committed_at": _optional_utc(row.committed_at),
                        "revoked_at": _optional_utc(row.revoked_at),
                        "updated_at": _as_utc(row.updated_at),
                    }
                    for row in batch_rows
                ],
                "data_artifacts": [
                    {
                        "id": row.id,
                        "batch_id": row.batch_id,
                        "filename": row.filename,
                        "content_type": row.content_type,
                        "byte_size": row.byte_size,
                        "sha256": row.sha256,
                        "storage_key": row.storage_key,
                        "updated_at": _as_utc(row.updated_at),
                    }
                    for row in artifact_rows
                ],
                "field_observations": [
                    {
                        "id": row.id,
                        "import_batch_id": row.import_batch_id,
                        "import_row_id": row.import_row_id,
                        "domain": row.domain,
                        "entity_key": row.entity_key,
                        "stat_date": row.stat_date,
                        "field_name": row.field_name,
                        "value": row.value,
                        "source_kind": row.source_kind.value,
                        "source_priority": row.source_priority,
                        "confirmed_sequence": row.confirmed_sequence,
                        "active": row.active,
                        "updated_at": _as_utc(row.updated_at),
                    }
                    for row in observation_rows
                ],
                "data_conflicts": [
                    {
                        "id": row.id,
                        "batch_id": row.batch_id,
                        "row_number": row.row_number,
                        "status": row.status.value,
                        "field_name": row.field_name,
                        "conflict_code": row.conflict_code,
                        "existing_value": row.existing_value,
                        "incoming_value": row.incoming_value,
                        "candidate_content_ids": row.candidate_content_ids,
                        "resolved_by_id": row.resolved_by_id,
                        "resolved_at": _optional_utc(row.resolved_at),
                        "updated_at": _as_utc(row.updated_at),
                    }
                    for row in conflict_rows
                ],
                "metric_snapshots": [
                    _model_watermark_projection(row) for row in metric_snapshots
                ],
                "account_metric_snapshots": [
                    _model_watermark_projection(row) for row in account_snapshots
                ],
                "audience_profile_snapshots": [
                    _model_watermark_projection(row) for row in audience_snapshots
                ],
                "audience_profile_items": [
                    _model_watermark_projection(row) for row in audience_items
                ],
                "benchmark_snapshots": [
                    _model_watermark_projection(row) for row in benchmark_snapshots
                ],
                "platform_content_records": [
                    _model_watermark_projection(row) for row in platform_contents
                ],
                "input": input.model_dump(mode="python"),
            },
        )
        return FreshnessStamp(
            policy_key=self.key,
            watermark_hash=watermark,
            expires_at=db_now + self._ttl,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


def _query_window(input: StageDataEnvelope) -> tuple[date | None, date | None]:
    value = input.data.get("query_window")
    if type(value) is not dict:
        return None, None
    start = value.get("start")
    end = value.get("end")
    if type(start) is not str or type(end) is not str:
        return None, None
    try:
        return date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return None, None


def _model_watermark_projection(row: Any) -> dict[str, Any]:
    return {
        column.name: _watermark_value(getattr(row, column.name))
        for column in row.__table__.columns
    }


def _watermark_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _as_utc(value)
    if type(value) is list:
        return [_watermark_value(item) for item in value]
    if type(value) is dict:
        return {key: _watermark_value(item) for key, item in value.items()}
    return value


# Validators are deliberately server-owned and query only the current database transaction.
_VALIDATORS: dict[str, FreshnessValidator] = {
    "account-snapshot/v1": _AccountDatabaseFreshnessValidator(
        "account-snapshot/v1", ttl=timedelta(minutes=15)
    ),
    "benchmark-evidence/v1": _AccountDatabaseFreshnessValidator(
        "benchmark-evidence/v1", ttl=timedelta(hours=1)
    ),
}


def get_freshness_validator(key: str) -> FreshnessValidator | None:
    return _VALIDATORS.get(key)


def require_freshness_validator(key: str) -> FreshnessValidator:
    validator = get_freshness_validator(key)
    if validator is None:
        raise LookupError(f"freshness_validator_missing:{key}")
    return validator


async def load_transaction_db_now(session: AsyncSession) -> datetime:
    dialect_name = session.bind.dialect.name if session.bind is not None else ""
    clock = (
        func.transaction_timestamp()
        if dialect_name == "postgresql"
        else func.current_timestamp()
    )
    value = (await session.execute(select(clock))).scalar_one()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise RuntimeError("database transaction clock returned an invalid value")
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def assess_checkpoint_freshness(
    session: AsyncSession,
    *,
    scope: RuntimeScope,
    step: CheckpointStepSpec,
    input: StageDataEnvelope,
    source_stamp: FreshnessStamp | None,
) -> FreshnessVerdict:
    if step.reuse_policy != "freshness_bound":
        return FreshnessVerdict(kind="reusable", reason=None)
    policy_key = step.freshness_policy_key
    validator = get_freshness_validator(policy_key or "")
    if validator is None:
        return FreshnessVerdict(kind="full_recompute", reason="freshness_validator_missing")
    if source_stamp is None or source_stamp.policy_key != policy_key:
        return FreshnessVerdict(kind="full_recompute", reason="freshness_stamp_missing")
    db_now = await load_transaction_db_now(session)
    current = await validator.current_stamp(
        session,
        scope=scope,
        step=step,
        input=input,
        db_now=db_now,
    )
    if db_now > source_stamp.expires_at:
        return FreshnessVerdict(
            kind="full_recompute",
            reason="freshness_expired",
            validated_at=db_now,
            stamp=current,
        )
    if current.policy_key != policy_key or current.watermark_hash != source_stamp.watermark_hash:
        return FreshnessVerdict(
            kind="full_recompute",
            reason="freshness_watermark_changed",
            validated_at=db_now,
            stamp=current,
        )
    return FreshnessVerdict(
        kind="reusable",
        reason=None,
        validated_at=db_now,
        stamp=current,
    )


__all__ = [
    "FreshnessValidator",
    "FreshnessVerdict",
    "assess_checkpoint_freshness",
    "get_freshness_validator",
    "load_transaction_db_now",
    "require_freshness_validator",
]
