from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Account,
    AccountMetricSnapshot,
    AudienceProfileSnapshot,
    BenchmarkSnapshot,
    DataConflict,
    DataFieldObservation,
    DataImportBatch,
    DataImportRow,
    MetricSnapshot,
)
from app.models.enums import ConflictStatus, DataSourceKind, ImportBatchStatus, MetricSource
from app.services.data_import.merge import account_entity_key, content_entity_key
from app.services.data_import.projection import decode_observation_value

ObservationSource = DataSourceKind | Literal["legacy_manual", "derived"]

CONTENT_METRICS = (
    "play",
    "exposure",
    "completion_rate",
    "like_rate",
    "comment_rate",
    "share_rate",
    "follower_delta",
    "like_count",
    "comment_count",
    "share_count",
    "favorite_count",
    "cover_click_rate",
    "avg_watch_time_seconds",
    "completion_rate_5s",
    "bounce_rate_2s",
    "profile_visit_count",
)
ACCOUNT_METRICS = (
    "play",
    "exposure",
    "follower_count",
    "follower_delta",
    "engagement_rate",
    "profile_visit_count",
    "unfollow_count",
    "like_count",
    "comment_count",
    "share_count",
    "cover_click_rate",
)
CONTENT_DIRECT_FIELDS = {
    "play": "play",
    "exposure": "exposure",
    "completion_rate": "completion_rate",
    "follower_delta": "follower_delta",
    "like_count": "like_count",
    "comment_count": "comment_count",
    "share_count": "share_count",
    "favorite_count": "favorite_count",
    "cover_click_rate": "cover_click_rate",
    "avg_watch_time_seconds": "avg_watch_time_seconds",
    "completion_rate_5s": "completion_rate_5s",
    "bounce_rate_2s": "bounce_rate_2s",
    "profile_visit_count": "profile_visit_count",
}
CONTENT_DERIVED_FIELDS = {
    "like_rate": "like_count",
    "comment_rate": "comment_count",
    "share_rate": "share_count",
}
ACCOUNT_FIELDS = {
    "play": "total_play",
    "exposure": "total_exposure",
    "follower_count": "follower_count",
    "follower_delta": "follower_delta",
    "engagement_rate": "engagement_rate",
    "profile_visit_count": "profile_visit_count",
    "unfollow_count": "unfollow_count",
    "like_count": "like_count",
    "comment_count": "comment_count",
    "share_count": "share_count",
    "cover_click_rate": "cover_click_rate",
}
OFFICIAL_SOURCES = {
    MetricSource.DOUYIN,
    MetricSource.XIAOHONGSHU,
    MetricSource.SHIPINHAO,
}


@dataclass(slots=True)
class AccountDataObservation:
    metric: str
    value: int | float | None
    source: ObservationSource
    observed_at: date
    confirmed_at: datetime | None
    evidence_id: int
    evidence_kind: str


@dataclass(slots=True)
class AccountDataMetric:
    metric: str
    value: int | float | None = None
    source: ObservationSource | None = None
    observations: list[AccountDataObservation] = field(default_factory=list)


@dataclass(slots=True)
class ContentMetricSnapshotView:
    stat_date: date
    title: str | None
    content_item_id: int | None
    platform_content_record_id: int | None
    has_stable_identity: bool
    content_format: str | None
    review_status: str | None
    metrics: dict[str, AccountDataMetric]


@dataclass(slots=True)
class AccountMetricSnapshotView:
    stat_date: date
    metrics: dict[str, AccountDataMetric]


@dataclass(slots=True)
class AudienceProfileItemView:
    label: str
    value: str
    ratio: float | None
    rank: int


@dataclass(slots=True)
class AudienceProfileSnapshotView:
    stat_date: date
    dimension: str
    total_audience: int | None
    source: ObservationSource
    confirmed_at: datetime | None
    items: list[AudienceProfileItemView]


@dataclass(slots=True)
class BenchmarkSnapshotView:
    stat_date: date
    benchmark_code: str
    metric_code: str
    metric_value: float | None
    sample_size: int | None
    source: ObservationSource
    confirmed_at: datetime | None
    meta: dict


@dataclass(slots=True)
class ConflictView:
    id: int
    batch_id: int
    row_number: int
    field_name: str
    conflict_code: str
    message: str
    created_at: datetime


@dataclass(slots=True)
class AccountDataFreshness:
    latest_observed_at: date | None
    latest_confirmed_at: datetime | None
    days_since_observed: int | None
    days_since_confirmed: int | None


@dataclass(slots=True)
class AccountDataSourceSummary:
    batch_id: int | None
    source_kind: str
    data_domains: list[str]
    confirmed_at: datetime | None
    period_start: date | None
    period_end: date | None


@dataclass(slots=True)
class AccountDataView:
    coverage: dict[str, str]
    freshness: AccountDataFreshness
    conflicts: list[ConflictView]
    content_snapshots: list[ContentMetricSnapshotView]
    account_snapshots: list[AccountMetricSnapshotView]
    audience: list[AudienceProfileSnapshotView]
    benchmarks: list[BenchmarkSnapshotView]
    evidence_rows: list[MetricSnapshot]
    latest_synced_at: datetime | None
    latest_confirmed_at: datetime | None
    source_summary: list[AccountDataSourceSummary]


class _SourceSummaryPayload(TypedDict):
    data_domains: set[str]
    confirmed_at: datetime | None
    period_start: date | None
    period_end: date | None


@dataclass(slots=True)
class _ImportProjectionContext:
    batch: DataImportBatch
    row: DataImportRow


FieldObservationIndex = dict[
    tuple[str, str, date, str],
    list[DataFieldObservation],
]


class AccountDataViewService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def load(
        self,
        account: Account,
        period_start: date,
        period_end: date,
    ) -> AccountDataView:
        content_rows = list(
            await self.session.scalars(
                select(MetricSnapshot)
                .options(selectinload(MetricSnapshot.platform_content_record))
                .where(
                    MetricSnapshot.org_id == account.org_id,
                    MetricSnapshot.account_id == account.id,
                    MetricSnapshot.source != MetricSource.DEMO,
                    MetricSnapshot.stat_date.between(period_start, period_end),
                )
                .order_by(MetricSnapshot.stat_date.desc(), MetricSnapshot.id.desc())
            )
        )
        account_rows = list(
            await self.session.scalars(
                select(AccountMetricSnapshot)
                .where(
                    AccountMetricSnapshot.org_id == account.org_id,
                    AccountMetricSnapshot.account_id == account.id,
                    AccountMetricSnapshot.stat_date.between(period_start, period_end),
                )
                .order_by(AccountMetricSnapshot.stat_date.desc(), AccountMetricSnapshot.id.desc())
            )
        )
        audience_rows = list(
            await self.session.scalars(
                select(AudienceProfileSnapshot)
                .options(selectinload(AudienceProfileSnapshot.items))
                .where(
                    AudienceProfileSnapshot.org_id == account.org_id,
                    AudienceProfileSnapshot.account_id == account.id,
                    AudienceProfileSnapshot.stat_date.between(period_start, period_end),
                )
                .order_by(
                    AudienceProfileSnapshot.stat_date.desc(),
                    AudienceProfileSnapshot.id.desc(),
                )
            )
        )
        benchmark_rows = list(
            await self.session.scalars(
                select(BenchmarkSnapshot)
                .where(
                    BenchmarkSnapshot.org_id == account.org_id,
                    BenchmarkSnapshot.account_id == account.id,
                    BenchmarkSnapshot.stat_date.between(period_start, period_end),
                )
                .order_by(BenchmarkSnapshot.stat_date.desc(), BenchmarkSnapshot.id.desc())
            )
        )
        field_observations = list(
            await self.session.scalars(
                select(DataFieldObservation).where(
                    DataFieldObservation.org_id == account.org_id,
                    DataFieldObservation.account_id == account.id,
                    DataFieldObservation.stat_date.between(period_start, period_end),
                    DataFieldObservation.active.is_(True),
                )
            )
        )
        field_observation_index = _build_field_observation_index(field_observations)
        relevant_batch_ids = _collect_relevant_batch_ids(
            content_rows=content_rows,
            account_rows=account_rows,
            audience_rows=audience_rows,
            benchmark_rows=benchmark_rows,
        )
        relevant_batch_ids.update(item.import_batch_id for item in field_observations)
        batches = await self._load_batches(account, relevant_batch_ids)
        batch_by_id = {item.id: item for item in batches}
        import_rows = _build_import_projection_index(batches)
        conflicts = await self._load_conflicts(
            account,
            relevant_batch_ids,
            period_start,
            period_end,
            batches,
        )
        content_snapshots = _build_content_snapshots(
            content_rows,
            batch_by_id,
            import_rows,
            field_observation_index,
        )
        account_snapshots = _build_account_snapshots(
            account_rows,
            batch_by_id,
            import_rows,
            field_observation_index,
        )
        audience = _build_audience_views(audience_rows, batch_by_id)
        benchmarks = _build_benchmark_views(benchmark_rows, batch_by_id)
        coverage = {
            "account_metrics": "available" if account_snapshots else "missing",
            "content_metrics": "available" if content_snapshots else "missing",
            "content_identity": _content_identity_coverage(content_snapshots),
            "audience": "available" if audience else "missing",
            "benchmarks": "available" if benchmarks else "missing",
        }
        latest_synced_at = _latest_synced_at(
            content_rows=content_rows,
            account_rows=account_rows,
            audience_rows=audience_rows,
            benchmark_rows=benchmark_rows,
        )
        latest_confirmed_at = _latest_confirmed_at(
            content_rows=content_rows,
            account_rows=account_rows,
            audience_rows=audience_rows,
            benchmark_rows=benchmark_rows,
            batch_by_id=batch_by_id,
        )
        freshness = _build_freshness(
            period_end=period_end,
            latest_observed_at=_latest_observed_at(
                content_rows=content_rows,
                account_rows=account_rows,
                audience_rows=audience_rows,
                benchmark_rows=benchmark_rows,
            ),
            latest_confirmed_at=latest_confirmed_at,
        )
        return AccountDataView(
            coverage=coverage,
            freshness=freshness,
            conflicts=conflicts,
            content_snapshots=content_snapshots,
            account_snapshots=account_snapshots,
            audience=audience,
            benchmarks=benchmarks,
            evidence_rows=content_rows,
            latest_synced_at=latest_synced_at,
            latest_confirmed_at=latest_confirmed_at,
            source_summary=_build_source_summary(
                content_rows=content_rows,
                account_rows=account_rows,
                audience_rows=audience_rows,
                benchmark_rows=benchmark_rows,
                batch_by_id=batch_by_id,
            ),
        )

    async def _load_batches(
        self,
        account: Account,
        batch_ids: set[int],
    ) -> list[DataImportBatch]:
        if not batch_ids:
            return []
        return list(
            await self.session.scalars(
                select(DataImportBatch)
                .options(selectinload(DataImportBatch.rows))
                .where(
                    DataImportBatch.org_id == account.org_id,
                    DataImportBatch.account_id == account.id,
                    DataImportBatch.id.in_(batch_ids),
                    DataImportBatch.status == ImportBatchStatus.COMMITTED,
                    DataImportBatch.committed_at.is_not(None),
                    DataImportBatch.revoked_at.is_(None),
                )
                .order_by(DataImportBatch.committed_at.desc(), DataImportBatch.id.desc())
            )
        )

    async def _load_conflicts(
        self,
        account: Account,
        batch_ids: set[int],
        period_start: date,
        period_end: date,
        batches: list[DataImportBatch],
    ) -> list[ConflictView]:
        if not batch_ids:
            return []
        batch_by_id = {item.id: item for item in batches}
        row_index = {
            (batch.id, row.row_number): row
            for batch in batches
            for row in batch.rows
        }
        rows = list(
            await self.session.scalars(
                select(DataConflict)
                .join(DataImportBatch, DataConflict.batch_id == DataImportBatch.id)
                .where(
                    DataConflict.org_id == account.org_id,
                    DataConflict.account_id == account.id,
                    DataConflict.batch_id.in_(batch_ids),
                    DataConflict.status == ConflictStatus.OPEN,
                    DataImportBatch.org_id == account.org_id,
                    DataImportBatch.account_id == account.id,
                    DataImportBatch.revoked_at.is_(None),
                )
                .order_by(DataConflict.created_at.desc(), DataConflict.id.desc())
            )
        )
        return [
            ConflictView(
                id=row.id,
                batch_id=row.batch_id,
                row_number=row.row_number,
                field_name=row.field_name,
                conflict_code=row.conflict_code,
                message=row.message,
                created_at=row.created_at,
            )
            for row in rows
            if _conflict_overlaps_period(
                conflict=row,
                batch_by_id=batch_by_id,
                row_index=row_index,
                period_start=period_start,
                period_end=period_end,
            )
        ]


def _build_import_projection_index(
    batches: list[DataImportBatch],
) -> dict[str, dict[int, _ImportProjectionContext]]:
    index: dict[str, dict[int, _ImportProjectionContext]] = defaultdict(dict)
    for batch in batches:
        for row in batch.rows:
            for target in row.projected_target_ids or []:
                kind = target.get("kind")
                target_id = target.get("id")
                if not kind or target_id is None:
                    continue
                index[str(kind)][int(target_id)] = _ImportProjectionContext(batch=batch, row=row)
    return index


def _build_field_observation_index(
    observations: list[DataFieldObservation],
) -> FieldObservationIndex:
    index: FieldObservationIndex = defaultdict(list)
    for observation in observations:
        index[
            (
                observation.domain,
                observation.entity_key,
                observation.stat_date,
                observation.field_name,
            )
        ].append(observation)
    return index


def _build_content_snapshots(
    rows: list[MetricSnapshot],
    batch_by_id: dict[int, DataImportBatch],
    import_rows: dict[str, dict[int, _ImportProjectionContext]],
    field_observations: FieldObservationIndex,
) -> list[ContentMetricSnapshotView]:
    grouped: dict[tuple[object, date], ContentMetricSnapshotView] = {}
    for row in rows:
        imported_context = import_rows.get("metric_snapshot", {}).get(row.id)
        snapshot_key = _content_snapshot_key(row=row, imported_context=imported_context)
        snapshot = grouped.get(snapshot_key)
        if snapshot is None:
            snapshot = ContentMetricSnapshotView(
                stat_date=row.stat_date,
                title=row.title,
                content_item_id=row.content_item_id,
                platform_content_record_id=row.platform_content_record_id,
                has_stable_identity=(
                    row.platform_content_record_id is not None or row.content_item_id is not None
                ),
                content_format=(
                    row.platform_content_record.content_format
                    if row.platform_content_record is not None
                    else None
                ),
                review_status=(
                    row.platform_content_record.review_status
                    if row.platform_content_record is not None
                    else None
                ),
                metrics={metric: AccountDataMetric(metric=metric) for metric in CONTENT_METRICS},
            )
            grouped[snapshot_key] = snapshot
        for metric_name in CONTENT_METRICS:
            persisted_observations: list[DataFieldObservation] = []
            if row.platform_content_record_id is not None:
                persisted_observations = field_observations.get(
                    (
                        "content_metrics",
                        content_entity_key(
                            int(row.account_id),
                            row.platform_content_record_id,
                        ),
                        row.stat_date,
                        CONTENT_DIRECT_FIELDS.get(metric_name, metric_name),
                    ),
                    [],
                )
            if persisted_observations and row.import_batch_id is not None:
                known_ids = {
                    item.evidence_id
                    for item in snapshot.metrics[metric_name].observations
                    if item.evidence_kind == "field_observation"
                }
                snapshot.metrics[metric_name].observations.extend(
                    _field_observation_view(metric_name, item, batch_by_id)
                    for item in persisted_observations
                    if item.id not in known_ids
                )
            else:
                observation = _content_observation(
                    metric_name=metric_name,
                    row=row,
                    batch_by_id=batch_by_id,
                    imported_context=imported_context,
                )
                if observation is not None:
                    snapshot.metrics[metric_name].observations.append(observation)
    results = list(grouped.values())
    for snapshot in results:
        for metric in snapshot.metrics.values():
            _finalize_metric(metric)
    results.sort(
        key=lambda item: (
            item.stat_date,
            item.platform_content_record_id or 0,
            item.content_item_id or 0,
            item.title or "",
        ),
        reverse=True,
    )
    return results


def _content_snapshot_key(
    *,
    row: MetricSnapshot,
    imported_context: _ImportProjectionContext | None,
) -> tuple[object, date]:
    if row.platform_content_record_id is not None:
        return (("platform_content_record", row.platform_content_record_id), row.stat_date)
    if row.content_item_id is not None:
        return (("content_item", row.content_item_id), row.stat_date)
    if imported_context is not None:
        return (("import_row", imported_context.row.id), row.stat_date)
    return (("metric_snapshot", row.id), row.stat_date)


def _content_identity_coverage(content_snapshots: list[ContentMetricSnapshotView]) -> str:
    if not content_snapshots:
        return "missing"
    if any(not item.has_stable_identity for item in content_snapshots):
        return "ambiguous"
    return "available"


def _build_account_snapshots(
    rows: list[AccountMetricSnapshot],
    batch_by_id: dict[int, DataImportBatch],
    import_rows: dict[str, dict[int, _ImportProjectionContext]],
    field_observations: FieldObservationIndex,
) -> list[AccountMetricSnapshotView]:
    grouped: dict[date, AccountMetricSnapshotView] = {}
    for row in rows:
        context = import_rows.get("account_metric_snapshot", {}).get(row.id)
        snapshot = grouped.setdefault(
            row.stat_date,
            AccountMetricSnapshotView(
                stat_date=row.stat_date,
                metrics={
                    metric: AccountDataMetric(metric=metric)
                    for metric in ACCOUNT_METRICS
                },
            ),
        )
        for metric_name in ACCOUNT_METRICS:
            persisted_observations = field_observations.get(
                (
                    "account_metrics",
                    account_entity_key(row.account_id),
                    row.stat_date,
                    ACCOUNT_FIELDS[metric_name],
                ),
                [],
            )
            if persisted_observations:
                known_ids = {
                    item.evidence_id
                    for item in snapshot.metrics[metric_name].observations
                    if item.evidence_kind == "field_observation"
                }
                snapshot.metrics[metric_name].observations.extend(
                    _field_observation_view(metric_name, item, batch_by_id)
                    for item in persisted_observations
                    if item.id not in known_ids
                )
            else:
                observation = _account_observation(
                    metric_name=metric_name,
                    row=row,
                    batch_by_id=batch_by_id,
                    imported_context=context,
                )
                if observation is not None:
                    snapshot.metrics[metric_name].observations.append(observation)
    results = list(grouped.values())
    for snapshot in results:
        for metric in snapshot.metrics.values():
            _finalize_metric(metric)
    results.sort(key=lambda item: item.stat_date, reverse=True)
    return results


def _field_observation_view(
    metric_name: str,
    observation: DataFieldObservation,
    batch_by_id: dict[int, DataImportBatch],
) -> AccountDataObservation:
    batch = batch_by_id.get(observation.import_batch_id)
    return AccountDataObservation(
        metric=metric_name,
        value=_coerce_metric_value(
            metric_name,
            decode_observation_value(observation.value),
        ),
        source=observation.source_kind,
        observed_at=observation.stat_date,
        confirmed_at=(
            batch.committed_at if batch is not None else observation.updated_at
        ),
        evidence_id=observation.id,
        evidence_kind="field_observation",
    )


def _build_audience_views(
    rows: list[AudienceProfileSnapshot],
    batch_by_id: dict[int, DataImportBatch],
) -> list[AudienceProfileSnapshotView]:
    selected: dict[tuple[date, str], AudienceProfileSnapshot] = {}
    for row in rows:
        key = (row.stat_date, row.dimension)
        existing = selected.get(key)
        if existing is None or _projection_row_sort_key(
            row_source=(
                batch_by_id[row.import_batch_id].source_kind
                if row.import_batch_id in batch_by_id
                else row.source_kind
            ),
            confirmed_at=(
                batch_by_id[row.import_batch_id].committed_at
                if row.import_batch_id in batch_by_id
                else row.updated_at
            ),
            row_id=row.id,
        ) < _projection_row_sort_key(
            row_source=(
                batch_by_id[existing.import_batch_id].source_kind
                if existing.import_batch_id in batch_by_id
                else existing.source_kind
            ),
            confirmed_at=(
                batch_by_id[existing.import_batch_id].committed_at
                if existing.import_batch_id in batch_by_id
                else existing.updated_at
            ),
            row_id=existing.id,
        ):
            selected[key] = row
    results: list[AudienceProfileSnapshotView] = []
    for row in selected.values():
        batch = batch_by_id.get(row.import_batch_id)
        results.append(
            AudienceProfileSnapshotView(
                stat_date=row.stat_date,
                dimension=row.dimension,
                total_audience=row.total_audience,
                source=batch.source_kind if batch is not None else row.source_kind,
                confirmed_at=batch.committed_at if batch is not None else row.updated_at,
                items=[
                    AudienceProfileItemView(
                        label=item.label,
                        value=item.value,
                        ratio=item.ratio,
                        rank=item.rank,
                    )
                    for item in row.items
                ],
            )
        )
    return results


def _build_benchmark_views(
    rows: list[BenchmarkSnapshot],
    batch_by_id: dict[int, DataImportBatch],
) -> list[BenchmarkSnapshotView]:
    selected: dict[tuple[date, str, str], BenchmarkSnapshot] = {}
    for row in rows:
        key = (row.stat_date, row.benchmark_code, row.metric_code)
        existing = selected.get(key)
        if existing is None or _projection_row_sort_key(
            row_source=(
                batch_by_id[row.import_batch_id].source_kind
                if row.import_batch_id in batch_by_id
                else row.source_kind
            ),
            confirmed_at=(
                batch_by_id[row.import_batch_id].committed_at
                if row.import_batch_id in batch_by_id
                else row.updated_at
            ),
            row_id=row.id,
        ) < _projection_row_sort_key(
            row_source=(
                batch_by_id[existing.import_batch_id].source_kind
                if existing.import_batch_id in batch_by_id
                else existing.source_kind
            ),
            confirmed_at=(
                batch_by_id[existing.import_batch_id].committed_at
                if existing.import_batch_id in batch_by_id
                else existing.updated_at
            ),
            row_id=existing.id,
        ):
            selected[key] = row
    results: list[BenchmarkSnapshotView] = []
    for row in selected.values():
        batch = batch_by_id.get(row.import_batch_id)
        results.append(
            BenchmarkSnapshotView(
                stat_date=row.stat_date,
                benchmark_code=row.benchmark_code,
                metric_code=row.metric_code,
                metric_value=row.metric_value,
                sample_size=row.sample_size,
                source=batch.source_kind if batch is not None else row.source_kind,
                confirmed_at=batch.committed_at if batch is not None else row.updated_at,
                meta=row.meta or {},
            )
        )
    return results


def _projection_row_sort_key(
    *,
    row_source: ObservationSource,
    confirmed_at: datetime | None,
    row_id: int,
) -> tuple[int, float, int]:
    return (
        _source_priority(row_source),
        -(
            confirmed_at.timestamp()
            if confirmed_at is not None
            else float("-inf")
        ),
        -row_id,
    )


def _content_observation(
    *,
    metric_name: str,
    row: MetricSnapshot,
    batch_by_id: dict[int, DataImportBatch],
    imported_context: _ImportProjectionContext | None,
) -> AccountDataObservation | None:
    if imported_context is not None:
        direct_field = CONTENT_DIRECT_FIELDS.get(metric_name)
        if direct_field is not None:
            if direct_field not in imported_context.row.normalized_values:
                return None
            value = _coerce_metric_value(
                metric_name,
                imported_context.row.normalized_values[direct_field],
            )
            source: ObservationSource = imported_context.batch.source_kind
        else:
            numerator_key = CONTENT_DERIVED_FIELDS.get(metric_name)
            if numerator_key is None:
                return None
            if numerator_key not in imported_context.row.normalized_values:
                return None
            if "play" not in imported_context.row.normalized_values:
                return None
            value = getattr(row, metric_name)
            source = "derived"
        return AccountDataObservation(
            metric=metric_name,
            value=value,
            source=source,
            observed_at=row.stat_date,
            confirmed_at=imported_context.batch.committed_at,
            evidence_id=row.id,
            evidence_kind="metric_snapshot",
        )
    value = getattr(row, metric_name)
    source = _metric_snapshot_source(row=row, batch_by_id=batch_by_id)
    return AccountDataObservation(
        metric=metric_name,
        value=value,
        source=source,
        observed_at=row.stat_date,
        confirmed_at=row.updated_at,
        evidence_id=row.id,
        evidence_kind="metric_snapshot",
    )


def _account_observation(
    *,
    metric_name: str,
    row: AccountMetricSnapshot,
    batch_by_id: dict[int, DataImportBatch],
    imported_context: _ImportProjectionContext | None,
) -> AccountDataObservation | None:
    field_name = ACCOUNT_FIELDS[metric_name]
    if imported_context is not None:
        normalized_key = "play" if metric_name == "play" else field_name
        if normalized_key not in imported_context.row.normalized_values:
            return None
        value = _coerce_metric_value(
            metric_name,
            imported_context.row.normalized_values[normalized_key],
        )
        confirmed_at = imported_context.batch.committed_at
        source: ObservationSource = imported_context.batch.source_kind
    else:
        value = getattr(row, field_name)
        batch = batch_by_id.get(row.import_batch_id)
        confirmed_at = batch.committed_at if batch is not None else row.updated_at
        source = batch.source_kind if batch is not None else row.source_kind
    return AccountDataObservation(
        metric=metric_name,
        value=value,
        source=source,
        observed_at=row.stat_date,
        confirmed_at=confirmed_at,
        evidence_id=row.id,
        evidence_kind="account_metric_snapshot",
    )


def _metric_snapshot_source(
    *,
    row: MetricSnapshot,
    batch_by_id: dict[int, DataImportBatch],
) -> ObservationSource:
    if row.import_batch_id is not None:
        batch = batch_by_id.get(row.import_batch_id)
        if batch is not None:
            return batch.source_kind
    if row.source in OFFICIAL_SOURCES:
        return DataSourceKind.OFFICIAL_API
    return "legacy_manual"


def _coerce_metric_value(metric_name: str, value) -> int | float | None:
    if value is None:
        return None
    if metric_name in {
        "play",
        "exposure",
        "follower_count",
        "follower_delta",
        "like_count",
        "comment_count",
        "share_count",
        "favorite_count",
        "profile_visit_count",
        "unfollow_count",
    }:
        return int(value)
    return float(value)


def _finalize_metric(metric: AccountDataMetric) -> None:
    metric.observations.sort(
        key=lambda item: (
            _source_priority(item.source),
            -(item.confirmed_at.timestamp() if item.confirmed_at is not None else float("-inf")),
            -item.observed_at.toordinal(),
            -item.evidence_id,
        )
    )
    selected = next((item for item in metric.observations if item.value is not None), None)
    if selected is None:
        metric.value = None
        metric.source = None
        return
    metric.value = selected.value
    metric.source = selected.source


def _source_priority(source: ObservationSource) -> int:
    if source == DataSourceKind.OFFICIAL_API:
        return 0
    if source in {
        DataSourceKind.PLATFORM_EXPORT,
        DataSourceKind.SCREENSHOT_VERIFIED,
        DataSourceKind.MANUAL_ENTRY,
    }:
        return 1
    if source == "legacy_manual":
        return 2
    return 3


def _latest_confirmed_at(
    *,
    content_rows: list[MetricSnapshot],
    account_rows: list[AccountMetricSnapshot],
    audience_rows: list[AudienceProfileSnapshot],
    benchmark_rows: list[BenchmarkSnapshot],
    batch_by_id: dict[int, DataImportBatch],
) -> datetime | None:
    candidates: list[datetime] = []
    for row in content_rows:
        batch = batch_by_id.get(row.import_batch_id) if row.import_batch_id is not None else None
        confirmed_at = batch.committed_at if batch is not None else row.updated_at
        if confirmed_at is not None:
            candidates.append(confirmed_at)
    for account_row in account_rows:
        batch = (
            batch_by_id.get(account_row.import_batch_id)
            if account_row.import_batch_id is not None
            else None
        )
        confirmed_at = batch.committed_at if batch is not None else account_row.updated_at
        if confirmed_at is not None:
            candidates.append(confirmed_at)
    for audience_row in audience_rows:
        batch = (
            batch_by_id.get(audience_row.import_batch_id)
            if audience_row.import_batch_id is not None
            else None
        )
        confirmed_at = batch.committed_at if batch is not None else audience_row.updated_at
        if confirmed_at is not None:
            candidates.append(confirmed_at)
    for benchmark_row in benchmark_rows:
        batch = (
            batch_by_id.get(benchmark_row.import_batch_id)
            if benchmark_row.import_batch_id is not None
            else None
        )
        confirmed_at = batch.committed_at if batch is not None else benchmark_row.updated_at
        if confirmed_at is not None:
            candidates.append(confirmed_at)
    if not candidates:
        return None
    return max(candidates, key=_datetime_sort_key)


def _latest_synced_at(
    *,
    content_rows: list[MetricSnapshot],
    account_rows: list[AccountMetricSnapshot],
    audience_rows: list[AudienceProfileSnapshot],
    benchmark_rows: list[BenchmarkSnapshot],
) -> datetime | None:
    candidates = [row.updated_at for row in content_rows]
    candidates.extend(row.updated_at for row in account_rows)
    candidates.extend(row.updated_at for row in audience_rows)
    candidates.extend(row.updated_at for row in benchmark_rows)
    dated = [item for item in candidates if item is not None]
    if not dated:
        return None
    return max(dated, key=_datetime_sort_key)


def _latest_observed_at(
    *,
    content_rows: list[MetricSnapshot],
    account_rows: list[AccountMetricSnapshot],
    audience_rows: list[AudienceProfileSnapshot],
    benchmark_rows: list[BenchmarkSnapshot],
) -> date | None:
    observed = [row.stat_date for row in content_rows]
    observed.extend(row.stat_date for row in account_rows)
    observed.extend(row.stat_date for row in audience_rows)
    observed.extend(row.stat_date for row in benchmark_rows)
    return max(observed, default=None)


def _build_freshness(
    *,
    period_end: date,
    latest_observed_at: date | None,
    latest_confirmed_at: datetime | None,
) -> AccountDataFreshness:
    return AccountDataFreshness(
        latest_observed_at=latest_observed_at,
        latest_confirmed_at=latest_confirmed_at,
        days_since_observed=(
            (period_end - latest_observed_at).days if latest_observed_at is not None else None
        ),
        days_since_confirmed=(
            (period_end - latest_confirmed_at.date()).days
            if latest_confirmed_at is not None
            else None
        ),
    )


def _build_source_summary(
    *,
    content_rows: list[MetricSnapshot],
    account_rows: list[AccountMetricSnapshot],
    audience_rows: list[AudienceProfileSnapshot],
    benchmark_rows: list[BenchmarkSnapshot],
    batch_by_id: dict[int, DataImportBatch],
) -> list[AccountDataSourceSummary]:
    grouped: dict[tuple[str, int | None], _SourceSummaryPayload] = {}
    for row in content_rows:
        _accumulate_source_summary(
            grouped=grouped,
            source=_metric_snapshot_source(row=row, batch_by_id=batch_by_id),
            batch=batch_by_id.get(row.import_batch_id) if row.import_batch_id is not None else None,
            data_domain="content_metrics",
            observed_at=row.stat_date,
            fallback_confirmed_at=row.updated_at,
        )
    for account_row in account_rows:
        batch = (
            batch_by_id.get(account_row.import_batch_id)
            if account_row.import_batch_id is not None
            else None
        )
        _accumulate_source_summary(
            grouped=grouped,
            source=batch.source_kind if batch is not None else account_row.source_kind,
            batch=batch,
            data_domain="account_metrics",
            observed_at=account_row.stat_date,
            fallback_confirmed_at=account_row.updated_at,
        )
    for audience_row in audience_rows:
        batch = (
            batch_by_id.get(audience_row.import_batch_id)
            if audience_row.import_batch_id is not None
            else None
        )
        _accumulate_source_summary(
            grouped=grouped,
            source=batch.source_kind if batch is not None else audience_row.source_kind,
            batch=batch,
            data_domain="audience",
            observed_at=audience_row.stat_date,
            fallback_confirmed_at=audience_row.updated_at,
        )
    for benchmark_row in benchmark_rows:
        batch = (
            batch_by_id.get(benchmark_row.import_batch_id)
            if benchmark_row.import_batch_id is not None
            else None
        )
        _accumulate_source_summary(
            grouped=grouped,
            source=batch.source_kind if batch is not None else benchmark_row.source_kind,
            batch=batch,
            data_domain="benchmarks",
            observed_at=benchmark_row.stat_date,
            fallback_confirmed_at=benchmark_row.updated_at,
        )
    results = [
        AccountDataSourceSummary(
            batch_id=batch_id,
            source_kind=source_kind,
            data_domains=sorted(payload["data_domains"]),
            confirmed_at=payload["confirmed_at"],
            period_start=payload["period_start"],
            period_end=payload["period_end"],
        )
        for (source_kind, batch_id), payload in grouped.items()
    ]
    results.sort(
        key=lambda item: (
            _datetime_sort_key(item.confirmed_at),
            item.source_kind,
            item.batch_id or 0,
        ),
        reverse=True,
    )
    return results


def _accumulate_source_summary(
    *,
    grouped: dict[tuple[str, int | None], _SourceSummaryPayload],
    source: ObservationSource,
    batch: DataImportBatch | None,
    data_domain: str,
    observed_at: date,
    fallback_confirmed_at: datetime | None,
) -> None:
    if source == "derived":
        return
    source_kind = source.value if isinstance(source, DataSourceKind) else str(source)
    batch_id = batch.id if batch is not None else None
    key = (source_kind, batch_id)
    payload = grouped.setdefault(
        key,
        {
            "data_domains": set(),
            "confirmed_at": batch.committed_at if batch is not None else fallback_confirmed_at,
            "period_start": batch.period_start if batch is not None else observed_at,
            "period_end": batch.period_end if batch is not None else observed_at,
        },
    )
    payload["data_domains"].add(data_domain)
    if batch is None:
        period_start = payload["period_start"]
        period_end = payload["period_end"]
        payload["period_start"] = (
            observed_at if period_start is None else min(period_start, observed_at)
        )
        payload["period_end"] = observed_at if period_end is None else max(period_end, observed_at)
    confirmed_at = batch.committed_at if batch is not None else fallback_confirmed_at
    if confirmed_at is not None and (
        payload["confirmed_at"] is None
        or _datetime_sort_key(confirmed_at) > _datetime_sort_key(payload["confirmed_at"])
    ):
        payload["confirmed_at"] = confirmed_at


def _collect_relevant_batch_ids(
    *,
    content_rows: list[MetricSnapshot],
    account_rows: list[AccountMetricSnapshot],
    audience_rows: list[AudienceProfileSnapshot],
    benchmark_rows: list[BenchmarkSnapshot],
) -> set[int]:
    batch_ids = {row.import_batch_id for row in content_rows if row.import_batch_id is not None}
    batch_ids.update(row.import_batch_id for row in account_rows if row.import_batch_id is not None)
    batch_ids.update(
        row.import_batch_id for row in audience_rows if row.import_batch_id is not None
    )
    batch_ids.update(
        row.import_batch_id for row in benchmark_rows if row.import_batch_id is not None
    )
    return batch_ids


def _conflict_overlaps_period(
    *,
    conflict: DataConflict,
    batch_by_id: dict[int, DataImportBatch],
    row_index: dict[tuple[int, int], DataImportRow],
    period_start: date,
    period_end: date,
) -> bool:
    row = row_index.get((conflict.batch_id, conflict.row_number))
    observed_at = _import_row_observed_at(row) if row is not None else None
    if observed_at is not None:
        return period_start <= observed_at <= period_end
    batch = batch_by_id.get(conflict.batch_id)
    if batch is None:
        return False
    return _periods_overlap(batch.period_start, batch.period_end, period_start, period_end)


def _import_row_observed_at(row: DataImportRow) -> date | None:
    raw_value = row.normalized_values.get("stat_date")
    if isinstance(raw_value, date):
        return raw_value
    if isinstance(raw_value, str):
        try:
            return date.fromisoformat(raw_value)
        except ValueError:
            return None
    return None


def _periods_overlap(
    left_start: date | None,
    left_end: date | None,
    right_start: date,
    right_end: date,
) -> bool:
    if left_start is None and left_end is None:
        return True
    start = left_start or left_end
    end = left_end or left_start
    if start is None or end is None:
        return True
    return start <= right_end and right_start <= end


def _datetime_sort_key(value: datetime | None) -> float:
    if value is None:
        return float("-inf")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).timestamp()
    return value.astimezone(UTC).timestamp()
