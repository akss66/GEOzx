from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePath
from typing import TypedDict
from urllib.parse import quote, unquote
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from app.core import storage
from app.models import (
    Account,
    AccountMetricSnapshot,
    AudienceProfileItem,
    AudienceProfileSnapshot,
    BenchmarkSnapshot,
    DataArtifact,
    DataConflict,
    DataFieldObservation,
    DataImportBatch,
    DataImportFile,
    DataImportRow,
    MetricSnapshot,
    PlatformContentRecord,
    User,
)
from app.models.account_data import CURRENT_IMPORT_PARSER_VERSION
from app.models.enums import (
    ConflictStatus,
    ContentIdentityConfidence,
    DataSourceKind,
    ImportBatchStatus,
    ImportRowStatus,
    MetricSource,
    Platform,
)
from app.services.data_import import REGISTERED_ADAPTERS, DataSourceAdapter, SourceInput
from app.services.data_import.identity import build_weak_fingerprint, match_content
from app.services.data_import.merge import (
    account_entity_key,
    audience_entity_key,
    benchmark_entity_key,
    content_entity_key,
)
from app.services.data_import.parser import SUPPORTED_EXTENSIONS, ParsedDataset, RowIssue
from app.services.data_import.projection import (
    ProjectionKey,
    deactivate_batch_observations,
    newest_winner,
    rebuild_projection,
    record_and_resolve_fields,
)
from app.services.data_import.templates import (
    DAILY_ACCOUNT_METRIC_TEMPLATE_CODES,
    KNOWN_TEMPLATES,
)

logger = logging.getLogger(__name__)


class DataImportBatchNotFoundError(LookupError):
    pass


class DataImportCommitConflictError(RuntimeError):
    pass


class DataImportStateError(RuntimeError):
    pass


class DataImportRevokeConflictError(RuntimeError):
    pass


class DataImportDeleteConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RowMatchResolution:
    selected_content_id: int | None
    resolved_by: User
    confirmed: bool | None = None


@dataclass(frozen=True, slots=True)
class ImportRowPage:
    items: list[DataImportRow]
    total_count: int
    filtered_count: int
    ready_count: int
    blocking_count: int


@dataclass(frozen=True, slots=True)
class ImportBatchListItem:
    batch: DataImportBatch
    created_by_name: str | None


@dataclass(frozen=True, slots=True)
class PreviewRecoveryResult:
    batch: DataImportBatch | None = None
    wrote_storage_key: str | None = None
    retired_stale_batch: bool = False


@dataclass(frozen=True, slots=True)
class QuarantinedArtifact:
    original: Path
    quarantined: Path


class _RevokeConflict(TypedDict):
    row_number: int
    message: str


async def create_preview(
    session: AsyncSession,
    *,
    user: User,
    account: Account,
    filename: str,
    content: bytes,
) -> DataImportBatch:
    _assert_org_account_scope(user=user, account=account)
    source: SourceInput = {"filename": filename, "data": content}
    adapter = _resolve_adapter(source)
    parsed = adapter.parse(source)
    rows = adapter.validate(adapter.normalize(parsed))
    preview = adapter.preview(rows, template_code=parsed.template_code)
    content_sha256 = hashlib.sha256(content).hexdigest()
    owns_transaction = not session.in_transaction()
    preview_scope = None if owns_transaction else await session.begin_nested()
    cleanup_storage_key: str | None = None

    try:
        existing = await _find_existing_preview(
            session,
            org_id=account.org_id,
            account_id=account.id,
            source_kind=adapter.source_kind,
            template_code=parsed.template_code,
            content_sha256=content_sha256,
        )
        if existing is not None:
            await _commit_preview_scope(
                session=session,
                owns_transaction=owns_transaction,
                preview_scope=preview_scope,
            )
            return existing

        recovery = await _recover_stale_preview(
            session,
            org_id=account.org_id,
            account_id=account.id,
            source_kind=adapter.source_kind,
            template_code=parsed.template_code,
            content_sha256=content_sha256,
            content=content,
        )
        if recovery.batch is not None:
            cleanup_storage_key = recovery.wrote_storage_key
            await _commit_preview_scope(
                session=session,
                owns_transaction=owns_transaction,
                preview_scope=preview_scope,
            )
            return recovery.batch

        extension = _validated_extension(filename)
        inserted = await _insert_preview_graph(
            session=session,
            user=user,
            account=account,
            parsed_template_code=parsed.template_code,
            preview_row_count=preview.total_rows,
            rows=rows,
            content_sha256=content_sha256,
            filename=filename,
            extension=extension,
            content=content,
            source_kind=adapter.source_kind,
        )
        if inserted is None:
            winner = await _find_existing_preview(
                session,
                org_id=account.org_id,
                account_id=account.id,
                source_kind=adapter.source_kind,
                template_code=parsed.template_code,
                content_sha256=content_sha256,
            )
            if winner is not None:
                await _commit_preview_scope(
                    session=session,
                    owns_transaction=owns_transaction,
                    preview_scope=preview_scope,
                )
                return winner

            recovery = await _recover_stale_preview(
                session,
                org_id=account.org_id,
                account_id=account.id,
                source_kind=adapter.source_kind,
                template_code=parsed.template_code,
                content_sha256=content_sha256,
                content=content,
            )
            if recovery.batch is not None:
                cleanup_storage_key = recovery.wrote_storage_key
                await _commit_preview_scope(
                    session=session,
                    owns_transaction=owns_transaction,
                    preview_scope=preview_scope,
                )
                return recovery.batch
            if recovery.retired_stale_batch:
                inserted = await _insert_preview_graph(
                    session=session,
                    user=user,
                    account=account,
                    parsed_template_code=parsed.template_code,
                    preview_row_count=preview.total_rows,
                    rows=rows,
                    content_sha256=content_sha256,
                    filename=filename,
                    extension=extension,
                    content=content,
                    source_kind=adapter.source_kind,
                )
                if inserted is None:
                    winner = await _find_existing_preview(
                        session,
                        org_id=account.org_id,
                        account_id=account.id,
                        source_kind=adapter.source_kind,
                        template_code=parsed.template_code,
                        content_sha256=content_sha256,
                    )
                    if winner is not None:
                        await _commit_preview_scope(
                            session=session,
                            owns_transaction=owns_transaction,
                            preview_scope=preview_scope,
                        )
                        return winner
            if inserted is None:
                raise RuntimeError(
                    "Preview dedupe conflict occurred but no winning preview was found"
                )

        batch_id, storage_key = inserted
        cleanup_storage_key = storage_key
        _write_artifact_atomic(storage_key, content)
        await _commit_preview_scope(
            session=session,
            owns_transaction=owns_transaction,
            preview_scope=preview_scope,
        )
    except Exception:
        await _rollback_preview_scope(
            session=session,
            owns_transaction=owns_transaction,
            preview_scope=preview_scope,
        )
        if cleanup_storage_key is not None:
            _delete_artifact(cleanup_storage_key)
        raise

    return await _load_batch(session, batch_id=batch_id)


async def create_job_dataset_preview(
    session: AsyncSession,
    *,
    user: User,
    account: Account,
    job_file: DataImportFile,
    dataset: ParsedDataset,
) -> DataImportBatch:
    _assert_org_account_scope(user=user, account=account)
    if (
        job_file.org_id != account.org_id
        or job_file.account_id != account.id
    ):
        raise ValueError("import file does not belong to the selected account")
    existing = await session.scalar(
        select(DataImportBatch).where(
            DataImportBatch.org_id == account.org_id,
            DataImportBatch.account_id == account.id,
            DataImportBatch.job_id == job_file.job_id,
            DataImportBatch.job_file_id == job_file.id,
            DataImportBatch.dataset_ordinal == dataset.dataset_ordinal,
        )
    )
    if existing is not None:
        return existing

    existing = await session.scalar(
        select(DataImportBatch)
        .where(
            DataImportBatch.org_id == account.org_id,
            DataImportBatch.account_id == account.id,
            DataImportBatch.source_kind == DataSourceKind.PLATFORM_EXPORT,
            DataImportBatch.template_code == dataset.template_code,
            DataImportBatch.content_sha256 == job_file.sha256,
            DataImportBatch.parser_version == CURRENT_IMPORT_PARSER_VERSION,
            DataImportBatch.committed_at.is_(None),
            DataImportBatch.revoked_at.is_(None),
        )
        .order_by(DataImportBatch.id.desc())
    )
    if existing is not None:
        return existing

    batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=user.id,
        job_id=job_file.job_id,
        job_file_id=job_file.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.PREVIEW_READY,
        template_code=dataset.template_code,
        content_sha256=job_file.sha256,
        parser_version=CURRENT_IMPORT_PARSER_VERSION,
        sheet_name=dataset.sheet_name,
        dataset_ordinal=dataset.dataset_ordinal,
        row_count=dataset.preview.total_rows,
        period_start=_derive_effective_period_boundary(
            dataset.rows,
            field_name="period_start",
            reducer=min,
        ),
        period_end=_derive_effective_period_boundary(
            dataset.rows,
            field_name="period_end",
            reducer=max,
        ),
    )
    session.add(batch)
    await session.flush()
    rows: list[DataImportRow] = []
    conflicts: list[DataConflict] = []
    for normalized_row in dataset.rows:
        row, conflict = await _build_row_persistence(
            session=session,
            account=account,
            batch=batch,
            row=normalized_row,
        )
        rows.append(row)
        if conflict is not None:
            conflicts.append(conflict)
    session.add_all([*rows, *conflicts])
    await session.flush()
    return batch


async def create_manual_preview(
    session: AsyncSession,
    *,
    user: User,
    account: Account,
    payload: dict,
    screenshot_filename: str | None = None,
    screenshot_content: bytes | None = None,
) -> DataImportBatch:
    _assert_org_account_scope(user=user, account=account)
    data_domain = str(payload["data_domain"])
    template_code = {
        "account_period_totals": "manual_account_period_v1",
        "audience_dimension": "manual_audience_dimension_v1",
        "benchmark": "manual_benchmark_v1",
    }.get(data_domain)
    if template_code is None:
        raise ValueError("Unsupported manual data domain")

    normalized_values = _manual_normalized_values(payload)
    canonical_payload = json.dumps(
        normalized_values,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    artifact_content = screenshot_content or b""
    content_sha256 = hashlib.sha256(canonical_payload + b"\0" + artifact_content).hexdigest()
    source_kind = (
        DataSourceKind.SCREENSHOT_VERIFIED
        if screenshot_content is not None
        else DataSourceKind.MANUAL_ENTRY
    )
    image_meta = None
    if screenshot_content is not None:
        image_meta = _validated_screenshot(
            screenshot_filename or "evidence.png",
            screenshot_content,
        )

    existing = await _find_active_preview(
        session,
        org_id=account.org_id,
        account_id=account.id,
        source_kind=source_kind,
        template_code=template_code,
        content_sha256=content_sha256,
    )
    if existing is not None:
        return existing

    owns_transaction = not session.in_transaction()
    preview_scope = None if owns_transaction else await session.begin_nested()
    storage_key: str | None = None
    try:
        batch = DataImportBatch(
            org_id=account.org_id,
            account_id=account.id,
            created_by_id=user.id,
            source_kind=source_kind,
            status=ImportBatchStatus.PREVIEW_READY,
            template_code=template_code,
            content_sha256=content_sha256,
            row_count=1,
            period_start=_coerce_date(payload.get("period_start")),
            period_end=(
                _coerce_date(payload.get("period_end"))
                or _coerce_date(payload["stat_date"])
            ),
        )
        session.add(batch)
        await session.flush()

        row_status = (
            ImportRowStatus.NEEDS_RESOLUTION
            if screenshot_content is not None
            else ImportRowStatus.READY
        )
        row = DataImportRow(
            org_id=account.org_id,
            account_id=account.id,
            batch_id=batch.id,
            row_number=1,
            status=row_status,
            raw_values=normalized_values,
            normalized_values=normalized_values,
            field_errors=[],
            warnings=[],
            candidate_content_ids=[],
            projected_target_ids=[],
        )
        session.add(row)

        if screenshot_content is not None and image_meta is not None:
            extension, content_type = image_meta
            storage_key = _build_storage_key(
                org_id=account.org_id,
                account_id=account.id,
                batch_id=batch.id,
                sha256=hashlib.sha256(screenshot_content).hexdigest(),
                extension=extension,
            )
            session.add(
                DataArtifact(
                    org_id=account.org_id,
                    account_id=account.id,
                    batch_id=batch.id,
                    filename=_sanitize_filename(
                        screenshot_filename or f"evidence{extension}",
                        extension=extension,
                    ),
                    content_type=content_type,
                    byte_size=len(screenshot_content),
                    sha256=hashlib.sha256(screenshot_content).hexdigest(),
                    storage_key=storage_key,
                )
            )
            session.add(
                DataConflict(
                    org_id=account.org_id,
                    account_id=account.id,
                    batch_id=batch.id,
                    row_number=1,
                    status=ConflictStatus.OPEN,
                    field_name="manual_confirmation",
                    conflict_code="screenshot_requires_confirmation",
                    message="Screenshot-backed values require explicit human confirmation",
                    incoming_value=normalized_values,
                    candidate_content_ids=[],
                )
            )

        await session.flush()
        if storage_key is not None and screenshot_content is not None:
            _write_artifact_atomic(storage_key, screenshot_content)
        await _commit_preview_scope(
            session=session,
            owns_transaction=owns_transaction,
            preview_scope=preview_scope,
        )
    except Exception:
        await _rollback_preview_scope(
            session=session,
            owns_transaction=owns_transaction,
            preview_scope=preview_scope,
        )
        if storage_key is not None:
            _delete_artifact(storage_key)
        raise

    return await _load_batch(session, batch_id=batch.id)


async def resolve_row_match(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row_number: int,
    resolution: RowMatchResolution,
) -> DataImportRow:
    _assert_batch_scope(batch=batch, user=resolution.resolved_by)
    row, conflict = await _load_resolution_targets(
        session=session,
        batch=batch,
        row_number=row_number,
    )
    if row is None:
        raise ValueError(f"Import row {row_number} does not exist in batch {batch.id}")
    if row.status is ImportRowStatus.INVALID:
        raise ValueError("Invalid rows cannot be resolved")
    if row.status is not ImportRowStatus.NEEDS_RESOLUTION or conflict is None:
        return _resolved_replay_or_error(row=row, conflict=conflict, resolution=resolution)
    if conflict.status is not ConflictStatus.OPEN:
        return _resolved_replay_or_error(row=row, conflict=conflict, resolution=resolution)

    if conflict.conflict_code == "screenshot_requires_confirmation":
        if resolution.confirmed is not True:
            raise ValueError("screenshot-backed values require explicit confirmation")
        if resolution.selected_content_id is not None:
            raise ValueError("screenshot confirmation does not accept a content candidate")
        row.status = ImportRowStatus.READY
        row.resolution_outcome = "confirmed"
        row.resolved_by_id = resolution.resolved_by.id
        row.resolved_at = datetime.now(UTC)
        conflict.status = ConflictStatus.RESOLVED
        conflict.resolved_by_id = resolution.resolved_by.id
        conflict.resolved_at = row.resolved_at
        await session.commit()
        return await _load_row(session, row_id=row.id)

    selected_content_id = resolution.selected_content_id
    if selected_content_id is not None:
        if selected_content_id not in row.candidate_content_ids:
            raise ValueError("selected candidate is not available for this import row")
        candidate = await session.get(PlatformContentRecord, selected_content_id)
        if (
            candidate is None
            or candidate.org_id != batch.org_id
            or candidate.account_id != batch.account_id
        ):
            raise ValueError("selected candidate is outside the batch account scope")

    row.status = ImportRowStatus.READY
    row.platform_content_record_id = selected_content_id
    row.resolution_outcome = "matched" if selected_content_id is not None else "no_match"
    row.resolved_by_id = resolution.resolved_by.id
    row.resolved_at = datetime.now(UTC)
    conflict.status = ConflictStatus.RESOLVED
    conflict.resolved_by_id = resolution.resolved_by.id
    conflict.resolved_at = row.resolved_at
    await session.commit()
    return await _load_row(session, row_id=row.id)


async def load_scoped_batch(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    batch_id: int,
) -> DataImportBatch:
    batch = await session.scalar(
        select(DataImportBatch)
        .options(
            selectinload(DataImportBatch.artifacts),
            selectinload(DataImportBatch.rows),
            selectinload(DataImportBatch.conflicts),
        )
        .where(
            DataImportBatch.org_id == org_id,
            DataImportBatch.account_id == account_id,
            DataImportBatch.id == batch_id,
        )
    )
    if batch is None:
        raise DataImportBatchNotFoundError("import batch does not exist")
    return batch


async def load_scoped_artifact(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    batch_id: int,
    artifact_id: int,
) -> DataArtifact:
    batch = await load_scoped_batch(
        session,
        org_id=org_id,
        account_id=account_id,
        batch_id=batch_id,
    )
    for artifact in batch.artifacts:
        if artifact.id == artifact_id:
            return artifact
    raise DataImportBatchNotFoundError("artifact does not exist")


async def list_scoped_batches(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
) -> list[ImportBatchListItem]:
    rows = await session.execute(
        select(DataImportBatch, User.display_name)
        .outerjoin(User, DataImportBatch.created_by_id == User.id)
        .where(
            DataImportBatch.org_id == org_id,
            DataImportBatch.account_id == account_id,
        )
        .order_by(DataImportBatch.id.desc())
    )
    return [
        ImportBatchListItem(batch=batch, created_by_name=created_by_name)
        for batch, created_by_name in rows.all()
    ]


async def load_scoped_import_rows(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    batch_id: int,
    page: int,
    page_size: int,
    view: str,
) -> ImportRowPage:
    scoped_batch_id = await session.scalar(
        select(DataImportBatch.id).where(
            DataImportBatch.org_id == org_id,
            DataImportBatch.account_id == account_id,
            DataImportBatch.id == batch_id,
        )
    )
    if scoped_batch_id is None:
        raise DataImportBatchNotFoundError("import batch does not exist")

    scope = (
        DataImportRow.org_id == org_id,
        DataImportRow.account_id == account_id,
        DataImportRow.batch_id == batch_id,
    )
    ready_statuses = (ImportRowStatus.READY, ImportRowStatus.COMMITTED)
    blocking_statuses = (ImportRowStatus.INVALID, ImportRowStatus.NEEDS_RESOLUTION)

    total_count = int(
        await session.scalar(select(func.count()).select_from(DataImportRow).where(*scope)) or 0
    )
    ready_count = int(
        await session.scalar(
            select(func.count())
            .select_from(DataImportRow)
            .where(*scope, DataImportRow.status.in_(ready_statuses))
        )
        or 0
    )
    blocking_count = int(
        await session.scalar(
            select(func.count())
            .select_from(DataImportRow)
            .where(*scope, DataImportRow.status.in_(blocking_statuses))
        )
        or 0
    )

    filtered_scope = list(scope)
    if view == "ready":
        filtered_scope.append(DataImportRow.status.in_(ready_statuses))
    elif view == "needs_work":
        filtered_scope.append(DataImportRow.status.in_(blocking_statuses))

    filtered_count = int(
        await session.scalar(
            select(func.count()).select_from(DataImportRow).where(*filtered_scope)
        )
        or 0
    )
    rows = await session.scalars(
        select(DataImportRow)
        .where(*filtered_scope)
        .order_by(DataImportRow.row_number.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return ImportRowPage(
        items=list(rows),
        total_count=total_count,
        filtered_count=filtered_count,
        ready_count=ready_count,
        blocking_count=blocking_count,
    )


async def account_status_summary(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
) -> dict[str, object]:
    batches = list(await session.scalars(
        select(DataImportBatch)
        .where(
            DataImportBatch.org_id == org_id,
            DataImportBatch.account_id == account_id,
            DataImportBatch.committed_at.is_not(None),
            DataImportBatch.revoked_at.is_(None),
        )
        .order_by(DataImportBatch.committed_at.desc(), DataImportBatch.id.desc())
    ))
    latest_confirmed_at = None
    coverage = {
        "account_metrics": "missing",
        "content_metrics": "missing",
        "audience_profiles": "missing",
        "benchmarks": "missing",
    }
    sources: list[dict[str, object]] = []
    seen_domains: set[str] = set()
    template_domains = {item.code: item.data_domain for item in KNOWN_TEMPLATES}
    for batch in batches:
        projected_domains = await _projected_domains_for_batch(session=session, batch=batch)
        if not projected_domains:
            continue
        if latest_confirmed_at is None:
            latest_confirmed_at = batch.committed_at
        data_domain = template_domains.get(batch.template_code, "unknown")
        if data_domain not in projected_domains:
            data_domain = next(iter(projected_domains))
        if data_domain not in seen_domains and data_domain in coverage:
            coverage[data_domain] = "available"
            seen_domains.add(data_domain)
        sources.append(
            {
                "batch_id": batch.id,
                "source_kind": batch.source_kind,
                "template_code": batch.template_code,
                "data_domain": data_domain,
                "committed_at": batch.committed_at,
                "period_start": batch.period_start,
                "period_end": batch.period_end,
            }
        )
    pending_batches = list(
        await session.scalars(
            select(DataImportBatch)
            .where(
                DataImportBatch.org_id == org_id,
                DataImportBatch.account_id == account_id,
                DataImportBatch.committed_at.is_(None),
                DataImportBatch.revoked_at.is_(None),
            )
            .order_by(DataImportBatch.updated_at.desc(), DataImportBatch.id.desc())
        )
    )
    pending_by_domain: dict[str, str] = {}
    for batch in pending_batches:
        data_domain = template_domains.get(batch.template_code)
        if data_domain is None or data_domain in pending_by_domain:
            continue
        pending_by_domain[data_domain] = (
            "failed"
            if batch.status is ImportBatchStatus.FAILED
            else "processing"
        )
    stale_before = datetime.now(UTC) - timedelta(days=45)
    dataset_inventory: list[dict[str, object]] = []
    for data_domain in coverage:
        domain_sources = [
            source for source in sources if source["data_domain"] == data_domain
        ]
        latest_source = domain_sources[0] if domain_sources else None
        period_starts = [
            source["period_start"]
            for source in domain_sources
            if isinstance(source["period_start"], date)
        ]
        period_ends = [
            source["period_end"]
            for source in domain_sources
            if isinstance(source["period_end"], date)
        ]
        confirmed_period_end = max(period_ends) if period_ends else None
        if latest_source is not None:
            committed_at = latest_source["committed_at"]
            freshness_date = (
                confirmed_period_end
                if confirmed_period_end is not None
                else committed_at.date()
                if isinstance(committed_at, datetime)
                else None
            )
            inventory_status = (
                "stale"
                if (
                    freshness_date is not None
                    and freshness_date < stale_before.date()
                )
                else "available"
            )
        else:
            inventory_status = pending_by_domain.get(data_domain, "not_imported")
        dataset_inventory.append(
            {
                "data_domain": data_domain,
                "status": inventory_status,
                "confirmed_period_start": min(period_starts) if period_starts else None,
                "confirmed_period_end": confirmed_period_end,
                "latest_source": latest_source,
            }
        )
    return {
        "account_id": account_id,
        "latest_confirmed_at": latest_confirmed_at,
        "coverage": coverage,
        "sources": sources,
        "dataset_inventory": dataset_inventory,
    }


async def commit_batch(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    batch_id: int,
    actor: User,
) -> DataImportBatch:
    batch = await _load_mutation_batch(
        session,
        org_id=org_id,
        account_id=account_id,
        batch_id=batch_id,
    )
    _assert_batch_scope(batch=batch, user=actor)
    if batch.revoked_at is not None or batch.status is ImportBatchStatus.REVOKED:
        raise DataImportStateError("revoked batches cannot be committed")
    if batch.committed_at is not None or batch.status is ImportBatchStatus.COMMITTED:
        return await load_scoped_batch(
            session,
            org_id=org_id,
            account_id=account_id,
            batch_id=batch_id,
        )
    if not batch.rows:
        raise DataImportCommitConflictError("batch contains no data rows")

    blocking_rows = [
        row.row_number
        for row in batch.rows
        if row.status in {ImportRowStatus.INVALID, ImportRowStatus.NEEDS_RESOLUTION}
    ]
    if blocking_rows:
        raise DataImportCommitConflictError(
            f"batch contains unresolved or invalid rows: {blocking_rows}"
        )

    try:
        account_query = select(Account).where(
            Account.org_id == org_id,
            Account.id == account_id,
        )
        if _dialect_name(session) == "postgresql":
            account_query = account_query.with_for_update()
        if await session.scalar(account_query) is None:
            raise DataImportStateError("account is no longer available")
        committed_at = datetime.now(UTC)
        batch.committed_at = committed_at
        batch.confirmed_sequence = int(committed_at.timestamp() * 1_000_000)
        for row in batch.rows:
            row.projected_target_ids = await _project_row_targets(
                session=session,
                batch=batch,
                row=row,
            )
            row.status = ImportRowStatus.COMMITTED
        batch.status = ImportBatchStatus.COMMITTED
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return await load_scoped_batch(
        session,
        org_id=org_id,
        account_id=account_id,
        batch_id=batch_id,
    )


async def revoke_batch(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    batch_id: int,
    actor: User,
) -> DataImportBatch:
    batch = await _load_mutation_batch(
        session,
        org_id=org_id,
        account_id=account_id,
        batch_id=batch_id,
    )
    _assert_batch_scope(batch=batch, user=actor)
    if batch.revoked_at is not None or batch.status is ImportBatchStatus.REVOKED:
        return batch
    if batch.committed_at is None or batch.status is not ImportBatchStatus.COMMITTED:
        raise DataImportStateError("only committed batches can be revoked")

    try:
        affected_keys = await deactivate_batch_observations(
            session,
            org_id=batch.org_id,
            account_id=batch.account_id,
            batch_id=batch.id,
        )
        if affected_keys:
            await _rebuild_canonical_projections(
                session=session,
                batch=batch,
                affected_keys=affected_keys,
            )
        else:
            for row in batch.rows:
                await _delete_row_targets(session=session, batch=batch, row=row)
        for row in batch.rows:
            row.status = ImportRowStatus.REVOKED
        batch.status = ImportBatchStatus.REVOKED
        batch.revoked_at = datetime.now(UTC)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    return await load_scoped_batch(
        session,
        org_id=org_id,
        account_id=account_id,
        batch_id=batch_id,
    )


async def delete_batch_permanently(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    batch_id: int,
    actor: User,
) -> None:
    batch = await _load_mutation_batch(
        session,
        org_id=org_id,
        account_id=account_id,
        batch_id=batch_id,
    )
    _assert_batch_scope(batch=batch, user=actor)
    if batch.status is ImportBatchStatus.COMMITTED:
        affected_keys = await deactivate_batch_observations(
            session,
            org_id=batch.org_id,
            account_id=batch.account_id,
            batch_id=batch.id,
        )
        if affected_keys:
            await _rebuild_canonical_projections(
                session=session,
                batch=batch,
                affected_keys=affected_keys,
            )
        else:
            conflicts = await _find_revoke_conflicts(session=session, batch=batch)
            if conflicts:
                raise DataImportDeleteConflictError("batch contains superseded projections")
            for row in batch.rows:
                await _delete_row_targets(session=session, batch=batch, row=row)

    storage_keys = [artifact.storage_key for artifact in batch.artifacts]
    quarantined = _quarantine_artifacts(storage_keys)
    try:
        await session.delete(batch)
        await session.commit()
    except Exception:
        await session.rollback()
        _restore_quarantined_artifacts(quarantined)
        raise

    try:
        _purge_quarantined_artifacts(quarantined)
    except OSError:
        logger.exception("failed to purge quarantined account data artifacts")


async def _insert_preview_graph(
    session: AsyncSession,
    *,
    user: User,
    account: Account,
    parsed_template_code: str,
    preview_row_count: int,
    rows: list,
    content_sha256: str,
    filename: str,
    extension: str,
    content: bytes,
    source_kind,
) -> tuple[int, str] | None:
    savepoint = await session.begin_nested()
    try:
        batch = DataImportBatch(
            org_id=account.org_id,
            account_id=account.id,
            created_by_id=user.id,
            source_kind=source_kind,
            status=ImportBatchStatus.PREVIEW_READY,
            template_code=parsed_template_code,
            content_sha256=content_sha256,
            parser_version=CURRENT_IMPORT_PARSER_VERSION,
            row_count=preview_row_count,
            period_start=_derive_effective_period_boundary(
                rows,
                field_name="period_start",
                reducer=min,
            ),
            period_end=_derive_effective_period_boundary(
                rows,
                field_name="period_end",
                reducer=max,
            ),
        )
        session.add(batch)
        await session.flush()

        storage_key = _build_storage_key(
            org_id=account.org_id,
            account_id=account.id,
            batch_id=batch.id,
            sha256=content_sha256,
            extension=extension,
        )
        artifact = DataArtifact(
            org_id=account.org_id,
            account_id=account.id,
            batch_id=batch.id,
            filename=_sanitize_filename(filename, extension=extension),
            content_type=_content_type_for_extension(extension),
            byte_size=len(content),
            sha256=content_sha256,
            storage_key=storage_key,
        )
        session.add(artifact)

        persisted_rows: list[DataImportRow] = []
        conflicts: list[DataConflict] = []
        for row in rows:
            persisted_row, conflict = await _build_row_persistence(
                session=session,
                account=account,
                batch=batch,
                row=row,
            )
            persisted_rows.append(persisted_row)
            if conflict is not None:
                conflicts.append(conflict)
        session.add_all([*persisted_rows, *conflicts])
        await session.flush()
    except IntegrityError:
        await savepoint.rollback()
        return None
    except Exception:
        await savepoint.rollback()
        raise

    await savepoint.commit()
    return batch.id, storage_key


async def _build_row_persistence(
    session: AsyncSession,
    *,
    account: Account,
    batch: DataImportBatch,
    row,
) -> tuple[DataImportRow, DataConflict | None]:
    field_errors = [_issue_to_dict(issue) for issue in row.errors]
    warnings = [_issue_to_dict(issue) for issue in row.warnings]
    conflict: DataConflict | None = None
    candidate_content_ids: list[int] = []
    platform_content_record_id: int | None = None
    weak_fingerprint = build_weak_fingerprint(
        title=row.normalized.get("title"),
        published_at=row.normalized.get("published_at"),
    )

    if row.errors:
        status = ImportRowStatus.INVALID
    else:
        matched = await match_content(
            session,
            account_id=account.id,
            platform=account.platform,
            normalized_row=row.normalized,
        )
        candidate_content_ids = matched.candidate_content_ids
        weak_fingerprint = matched.weak_fingerprint or weak_fingerprint
        if matched.confidence is ContentIdentityConfidence.CONFIRMED:
            status = ImportRowStatus.READY
            platform_content_record_id = matched.matched_content_id
        elif matched.confidence in {
            ContentIdentityConfidence.PROVISIONAL,
            ContentIdentityConfidence.AMBIGUOUS,
        }:
            status = ImportRowStatus.NEEDS_RESOLUTION
            conflict = DataConflict(
                org_id=batch.org_id,
                account_id=batch.account_id,
                batch_id=batch.id,
                row_number=row.row_number,
                status=ConflictStatus.OPEN,
                field_name="platform_content_record_id",
                conflict_code=(
                    "multiple_candidates"
                    if matched.confidence is ContentIdentityConfidence.AMBIGUOUS
                    else "provisional_candidate"
                ),
                message=(
                    "Multiple candidate content records require manual resolution"
                    if matched.confidence is ContentIdentityConfidence.AMBIGUOUS
                    else "Provisional content match requires manual confirmation"
                ),
                incoming_value=_json_ready(
                    {
                        "title": row.normalized.get("title"),
                        "published_at": row.normalized.get("published_at"),
                    }
                ),
                candidate_content_ids=candidate_content_ids,
            )
        else:
            status = ImportRowStatus.READY

    persisted = DataImportRow(
        org_id=batch.org_id,
        account_id=batch.account_id,
        batch_id=batch.id,
        row_number=row.row_number,
        status=status,
        raw_values=_json_ready(row.raw),
        normalized_values=_json_ready(row.normalized),
        field_errors=field_errors,
        warnings=warnings,
        candidate_content_ids=candidate_content_ids,
        projected_target_ids=[],
        platform_content_record_id=platform_content_record_id,
        weak_fingerprint=weak_fingerprint,
    )
    return persisted, conflict


async def _find_existing_preview(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    source_kind,
    template_code: str,
    content_sha256: str,
) -> DataImportBatch | None:
    existing = await session.scalar(
        select(DataImportBatch)
        .options(
            selectinload(DataImportBatch.artifacts),
            selectinload(DataImportBatch.rows),
            selectinload(DataImportBatch.conflicts),
        )
        .where(
            DataImportBatch.org_id == org_id,
            DataImportBatch.account_id == account_id,
            DataImportBatch.source_kind == source_kind,
            DataImportBatch.template_code == template_code,
            DataImportBatch.content_sha256 == content_sha256,
            DataImportBatch.parser_version == CURRENT_IMPORT_PARSER_VERSION,
            DataImportBatch.committed_at.is_(None),
            DataImportBatch.revoked_at.is_(None),
        )
        .order_by(DataImportBatch.id.desc())
    )
    if existing is None:
        return None
    artifact = existing.artifacts[0] if existing.artifacts else None
    if artifact is None or not storage.exists(artifact.storage_key):
        return None
    return existing


async def _recover_stale_preview(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    source_kind,
    template_code: str,
    content_sha256: str,
    content: bytes,
) -> PreviewRecoveryResult:
    existing = await _find_active_preview(
        session,
        org_id=org_id,
        account_id=account_id,
        source_kind=source_kind,
        template_code=template_code,
        content_sha256=content_sha256,
    )
    if existing is None:
        return PreviewRecoveryResult()

    if existing.parser_version != CURRENT_IMPORT_PARSER_VERSION:
        existing.status = ImportBatchStatus.REVOKED
        existing.revoked_at = datetime.now(UTC)
        await session.flush()
        return PreviewRecoveryResult(retired_stale_batch=True)

    artifact = existing.artifacts[0] if existing.artifacts else None
    if artifact is None:
        existing.status = ImportBatchStatus.REVOKED
        existing.revoked_at = datetime.now(UTC)
        await session.flush()
        return PreviewRecoveryResult(retired_stale_batch=True)
    if storage.exists(artifact.storage_key):
        return PreviewRecoveryResult()

    _write_artifact_atomic(artifact.storage_key, content)
    return PreviewRecoveryResult(batch=existing, wrote_storage_key=artifact.storage_key)


async def _find_active_preview(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    source_kind,
    template_code: str,
    content_sha256: str,
) -> DataImportBatch | None:
    return await session.scalar(
        select(DataImportBatch)
        .options(
            selectinload(DataImportBatch.artifacts),
            selectinload(DataImportBatch.rows),
            selectinload(DataImportBatch.conflicts),
        )
        .where(
            DataImportBatch.org_id == org_id,
            DataImportBatch.account_id == account_id,
            DataImportBatch.source_kind == source_kind,
            DataImportBatch.template_code == template_code,
            DataImportBatch.content_sha256 == content_sha256,
            DataImportBatch.committed_at.is_(None),
            DataImportBatch.revoked_at.is_(None),
        )
        .order_by(DataImportBatch.id.desc())
    )


async def _commit_preview_scope(
    session: AsyncSession,
    *,
    owns_transaction: bool,
    preview_scope,
) -> None:
    if owns_transaction:
        await session.commit()
    elif preview_scope is not None:
        await preview_scope.commit()


async def _rollback_preview_scope(
    session: AsyncSession,
    *,
    owns_transaction: bool,
    preview_scope,
) -> None:
    if owns_transaction:
        if session.in_transaction():
            await session.rollback()
    elif preview_scope is not None and preview_scope.is_active:
        await preview_scope.rollback()


async def _load_batch(session: AsyncSession, *, batch_id: int) -> DataImportBatch:
    batch = await session.scalar(
        select(DataImportBatch)
        .options(
            selectinload(DataImportBatch.artifacts),
            selectinload(DataImportBatch.rows),
            selectinload(DataImportBatch.conflicts),
        )
        .where(DataImportBatch.id == batch_id)
    )
    if batch is None:
        raise ValueError(f"Import batch {batch_id} no longer exists")
    return batch


async def _load_row(session: AsyncSession, *, row_id: int) -> DataImportRow:
    row = await session.scalar(select(DataImportRow).where(DataImportRow.id == row_id))
    if row is None:
        raise ValueError(f"Import row {row_id} no longer exists")
    return row


async def _load_resolution_targets(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row_number: int,
) -> tuple[DataImportRow | None, DataConflict | None]:
    row_query = select(DataImportRow).where(
        DataImportRow.org_id == batch.org_id,
        DataImportRow.account_id == batch.account_id,
        DataImportRow.batch_id == batch.id,
        DataImportRow.row_number == row_number,
    )
    conflict_query = select(DataConflict).where(
        DataConflict.org_id == batch.org_id,
        DataConflict.account_id == batch.account_id,
        DataConflict.batch_id == batch.id,
        DataConflict.row_number == row_number,
        DataConflict.field_name.in_({"platform_content_record_id", "manual_confirmation"}),
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        row_query = row_query.with_for_update()
        conflict_query = conflict_query.with_for_update()
    row = await session.scalar(row_query)
    conflict = await session.scalar(conflict_query)
    return row, conflict


async def _load_mutation_batch(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    batch_id: int,
) -> DataImportBatch:
    dialect_name = _dialect_name(session)
    if dialect_name == "postgresql":
        batch = await session.scalar(
            select(DataImportBatch)
            .where(
                DataImportBatch.org_id == org_id,
                DataImportBatch.account_id == account_id,
                DataImportBatch.id == batch_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if batch is None:
            raise DataImportBatchNotFoundError("import batch does not exist")
        rows = list(
            await session.scalars(
                select(DataImportRow)
                .where(
                    DataImportRow.org_id == org_id,
                    DataImportRow.account_id == account_id,
                    DataImportRow.batch_id == batch_id,
                )
                .order_by(DataImportRow.row_number)
                .with_for_update()
            )
        )
        conflicts = list(
            await session.scalars(
                select(DataConflict)
                .where(
                    DataConflict.org_id == org_id,
                    DataConflict.account_id == account_id,
                    DataConflict.batch_id == batch_id,
                )
                .order_by(DataConflict.id)
                .with_for_update()
            )
        )
        artifacts = list(
            await session.scalars(
                select(DataArtifact)
                .where(
                    DataArtifact.org_id == org_id,
                    DataArtifact.account_id == account_id,
                    DataArtifact.batch_id == batch_id,
                )
                .order_by(DataArtifact.id)
                .with_for_update()
            )
        )
        set_committed_value(batch, "artifacts", artifacts)
        set_committed_value(batch, "rows", rows)
        set_committed_value(batch, "conflicts", conflicts)
        return batch

    return await load_scoped_batch(
        session,
        org_id=org_id,
        account_id=account_id,
        batch_id=batch_id,
    )


async def _project_row_targets(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> list[dict[str, object]]:
    if batch.template_code == "douyin_work_list_v1":
        return await _project_work_list_row(session=session, batch=batch, row=row)
    if batch.template_code == "douyin_single_content_v1":
        return await _project_single_content_row(session=session, batch=batch, row=row)
    if batch.template_code in DAILY_ACCOUNT_METRIC_TEMPLATE_CODES:
        return await _project_daily_play_row(session=session, batch=batch, row=row)
    if batch.template_code == "douyin_period_aggregate_v1":
        return await _project_period_aggregate_row(session=session, batch=batch, row=row)
    if batch.template_code == "manual_account_period_v1":
        return await _project_daily_play_row(session=session, batch=batch, row=row)
    if batch.template_code == "manual_audience_dimension_v1":
        return await _project_audience_dimension_row(session=session, batch=batch, row=row)
    if batch.template_code == "manual_benchmark_v1":
        return await _project_manual_benchmark_row(session=session, batch=batch, row=row)
    raise DataImportStateError(f"unsupported template_code: {batch.template_code}")


async def _project_work_list_row(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> list[dict[str, object]]:
    content_record, targets = await _project_content_record(session=session, batch=batch, row=row)
    metric, action = await _upsert_metric_snapshot(
        session=session,
        batch=batch,
        row=row,
        content_record=content_record,
    )
    row.platform_content_record_id = content_record.id
    targets.append(
        {
            "kind": "metric_snapshot",
            "id": metric.id,
            "action": action,
        }
    )
    gap = _projection_gap(
        row=row,
        missing_fields=[
            "exposure",
        ],
    )
    if gap is not None:
        targets.append(gap)
    return targets


async def _project_single_content_row(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> list[dict[str, object]]:
    content_record, targets = await _project_content_record(session=session, batch=batch, row=row)
    metric, action = await _upsert_metric_snapshot(
        session=session,
        batch=batch,
        row=row,
        content_record=content_record,
    )
    row.platform_content_record_id = content_record.id
    targets.append(
        {
            "kind": "metric_snapshot",
            "id": metric.id,
            "action": action,
        }
    )
    return targets


async def _project_daily_play_row(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> list[dict[str, object]]:
    snapshot, action = await _upsert_account_metric_snapshot(
        session=session,
        batch=batch,
        row=row,
    )
    return [
        {
            "kind": "account_metric_snapshot",
            "id": snapshot.id,
            "action": action,
        }
    ]


async def _project_period_aggregate_row(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    for metric_code, metric_value in (
        ("click_rate", _float_or_none(row.normalized_values.get("click_rate"))),
        ("completion_rate_5s", _float_or_none(row.normalized_values.get("completion_rate_5s"))),
        ("bounce_rate_2s", _float_or_none(row.normalized_values.get("bounce_rate_2s"))),
        (
            "avg_watch_time_seconds",
            _float_or_none(row.normalized_values.get("avg_watch_time_seconds")),
        ),
        ("median_play", _float_or_none(row.normalized_values.get("median_play"))),
        ("avg_like_count", _float_or_none(row.normalized_values.get("avg_like_count"))),
        ("avg_comment_count", _float_or_none(row.normalized_values.get("avg_comment_count"))),
        ("avg_share_count", _float_or_none(row.normalized_values.get("avg_share_count"))),
    ):
        snapshot, action = await _upsert_benchmark_snapshot(
            session=session,
            batch=batch,
            row=row,
            metric_code=metric_code,
            metric_value=metric_value,
        )
        targets.append(
            {
                "kind": "benchmark_snapshot",
                "id": snapshot.id,
                "metric_code": metric_code,
                "action": action,
            }
        )
    return targets


async def _project_audience_dimension_row(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> list[dict[str, object]]:
    stat_date = _row_stat_date(batch=batch, row=row)
    dimension = str(row.normalized_values["dimension"])
    winners = await record_and_resolve_fields(
        session,
        batch=batch,
        row=row,
        domain="audience_profiles",
        entity_key=audience_entity_key(batch.account_id, dimension, "__profile__"),
        stat_date=stat_date,
        values={
            "total_audience": row.normalized_values.get("total_audience"),
            "items": row.normalized_values.get("audience_items"),
        },
    )
    snapshot = await session.scalar(
        select(AudienceProfileSnapshot)
        .options(selectinload(AudienceProfileSnapshot.items))
        .where(
            AudienceProfileSnapshot.org_id == batch.org_id,
            AudienceProfileSnapshot.account_id == batch.account_id,
            AudienceProfileSnapshot.stat_date == stat_date,
            AudienceProfileSnapshot.dimension == dimension,
        )
        .order_by(
            AudienceProfileSnapshot.updated_at.desc(),
            AudienceProfileSnapshot.id.desc(),
        )
    )
    action = "updated" if snapshot is not None else "created"
    if snapshot is None:
        snapshot = AudienceProfileSnapshot(
            org_id=batch.org_id,
            account_id=batch.account_id,
            import_batch_id=batch.id,
            source_kind=batch.source_kind,
            stat_date=stat_date,
            dimension=dimension,
            items=[],
        )
        session.add(snapshot)
        await session.flush()
    if "total_audience" in winners:
        snapshot.total_audience = _int_or_none(winners["total_audience"].value)
    if "items" in winners:
        snapshot.items = [
            AudienceProfileItem(
                org_id=batch.org_id,
                account_id=batch.account_id,
                snapshot_id=snapshot.id,
                label=str(item["label"]),
                value=str(item["value"]),
                ratio=_float_or_none(item.get("ratio")),
                rank=rank,
                meta={},
            )
            for rank, item in enumerate(winners["items"].value, start=1)
        ]
    provenance = newest_winner(winners)
    if provenance is not None:
        snapshot.import_batch_id = provenance.observation.import_batch_id
        snapshot.source_kind = provenance.observation.source_kind
    await session.flush()
    return [
        {
            "kind": "audience_profile_snapshot",
            "id": snapshot.id,
            "action": action,
        }
    ]


async def _project_manual_benchmark_row(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> list[dict[str, object]]:
    targets: list[dict[str, object]] = []
    for item in row.normalized_values.get("benchmark_metrics") or []:
        snapshot, action = await _upsert_benchmark_snapshot(
            session=session,
            batch=batch,
            row=row,
            metric_code=str(item["metric_code"]),
            metric_value=_float_or_none(item.get("metric_value")),
            benchmark_code=str(row.normalized_values["benchmark_code"]),
            sample_size=_int_or_none(item.get("sample_size")),
        )
        targets.append(
            {
                "kind": "benchmark_snapshot",
                "id": snapshot.id,
                "metric_code": snapshot.metric_code,
                "action": action,
            }
        )
    return targets


async def _ensure_platform_content_record(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> tuple[PlatformContentRecord, str]:
    if row.platform_content_record_id is not None:
        record = await session.get(PlatformContentRecord, row.platform_content_record_id)
        if (
            record is not None
            and record.org_id == batch.org_id
            and record.account_id == batch.account_id
        ):
            return record, "linked"

    existing = await session.scalar(
        select(PlatformContentRecord).where(
            PlatformContentRecord.account_id == batch.account_id,
            PlatformContentRecord.canonical_import_batch_id == batch.id,
            PlatformContentRecord.canonical_import_row_number == row.row_number,
        )
    )
    if existing is not None:
        return existing, "linked"

    published_at = row.normalized_values.get("published_at")
    if isinstance(published_at, str):
        published_at = datetime.fromisoformat(published_at)
    weak_fingerprint = row.weak_fingerprint or build_weak_fingerprint(
        title=row.normalized_values.get("title"),
        published_at=published_at,
    )
    record = PlatformContentRecord(
        org_id=batch.org_id,
        account_id=batch.account_id,
        platform=_batch_platform(batch),
        source_kind=batch.source_kind,
        source_metadata={
            "import_batch_id": batch.id,
            "row_number": row.row_number,
            "template_code": batch.template_code,
        },
        canonical_import_batch_id=batch.id,
        canonical_import_row_number=row.row_number,
        title=row.normalized_values.get("title"),
        published_at=published_at,
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
        weak_fingerprint=weak_fingerprint,
    )
    savepoint = await session.begin_nested()
    try:
        session.add(record)
        await session.flush()
    except IntegrityError:
        await savepoint.rollback()
        existing = await session.scalar(
            select(PlatformContentRecord).where(
                PlatformContentRecord.account_id == batch.account_id,
                PlatformContentRecord.canonical_import_batch_id == batch.id,
                PlatformContentRecord.canonical_import_row_number == row.row_number,
            )
        )
        if existing is None:
            raise
        return existing, "linked"
    await savepoint.commit()
    return record, "created"


async def _find_revoke_conflicts(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
) -> list[_RevokeConflict]:
    conflicts: list[_RevokeConflict] = []
    for row in batch.rows:
        for target in row.projected_target_ids:
            if target.get("kind") != "platform_content_record":
                continue
            if target.get("action") != "created":
                continue
            content_query = select(PlatformContentRecord).where(
                PlatformContentRecord.id == int(target["id"]),
                PlatformContentRecord.org_id == batch.org_id,
                PlatformContentRecord.account_id == batch.account_id,
            )
            if _dialect_name(session) == "postgresql":
                content_query = content_query.with_for_update()
            content = await session.scalar(content_query)
            if content is None:
                continue
            if content.canonical_import_batch_id != batch.id:
                conflicts.append(
                    {
                        "row_number": row.row_number,
                        "message": (
                            "A later import now owns this projected content record; "
                            "manual resolution is required before revoke."
                        ),
                    }
                )
    return conflicts


async def _record_revoke_conflicts(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    conflicts: list[_RevokeConflict],
) -> None:
    timestamp = datetime.now(UTC)
    for item in conflicts:
        existing = await session.scalar(
            select(DataConflict).where(
                DataConflict.org_id == batch.org_id,
                DataConflict.account_id == batch.account_id,
                DataConflict.batch_id == batch.id,
                DataConflict.row_number == item["row_number"],
                DataConflict.field_name == "projected_target_ids",
            )
        )
        if existing is not None:
            existing.status = ConflictStatus.OPEN
            existing.conflict_code = "superseded_projection"
            existing.message = str(item["message"])
            continue
        session.add(
            DataConflict(
                org_id=batch.org_id,
                account_id=batch.account_id,
                batch_id=batch.id,
                row_number=item["row_number"],
                status=ConflictStatus.OPEN,
                field_name="projected_target_ids",
                conflict_code="superseded_projection",
                message=str(item["message"]),
                incoming_value={"batch_id": batch.id, "detected_at": timestamp.isoformat()},
            )
        )


async def _delete_row_targets(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> None:
    for target in row.projected_target_ids:
        if target.get("kind") == "metric_snapshot":
            metric = await session.get(MetricSnapshot, int(target["id"]))
            if metric is not None and metric.import_batch_id == batch.id:
                await session.delete(metric)
        elif target.get("kind") == "account_metric_snapshot":
            account_snapshot = await session.get(AccountMetricSnapshot, int(target["id"]))
            if account_snapshot is not None and account_snapshot.import_batch_id == batch.id:
                await session.delete(account_snapshot)
        elif target.get("kind") == "benchmark_snapshot":
            benchmark_snapshot = await session.get(BenchmarkSnapshot, int(target["id"]))
            if benchmark_snapshot is not None and benchmark_snapshot.import_batch_id == batch.id:
                await session.delete(benchmark_snapshot)
        elif target.get("kind") == "audience_profile_snapshot":
            audience_snapshot = await session.get(AudienceProfileSnapshot, int(target["id"]))
            if audience_snapshot is not None and audience_snapshot.import_batch_id == batch.id:
                await session.delete(audience_snapshot)
        elif target.get("kind") == "platform_content_record" and target.get("action") == "created":
            content = await session.get(PlatformContentRecord, int(target["id"]))
            if content is not None and content.canonical_import_batch_id == batch.id:
                await session.delete(content)


async def _rebuild_canonical_projections(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    affected_keys: set[ProjectionKey],
) -> None:
    winners_by_key = await rebuild_projection(
        session,
        org_id=batch.org_id,
        account_id=batch.account_id,
        affected_keys=affected_keys,
    )
    for key, winners in winners_by_key.items():
        if key.domain == "account_metrics":
            await _rebuild_account_metric_projection(
                session=session,
                batch=batch,
                key=key,
                winners=winners,
            )
        elif key.domain == "content_metrics":
            await _rebuild_content_metric_projection(
                session=session,
                batch=batch,
                key=key,
                winners=winners,
            )
        elif key.domain == "audience_profiles":
            await _rebuild_audience_projection(
                session=session,
                batch=batch,
                key=key,
                winners=winners,
            )
        elif key.domain == "benchmarks":
            await _rebuild_benchmark_projection(
                session=session,
                batch=batch,
                key=key,
                winners=winners,
            )
    await session.flush()


async def _rebuild_account_metric_projection(
    *,
    session: AsyncSession,
    batch: DataImportBatch,
    key: ProjectionKey,
    winners,
) -> None:
    snapshots = list(
        await session.scalars(
            select(AccountMetricSnapshot)
            .where(
                AccountMetricSnapshot.org_id == batch.org_id,
                AccountMetricSnapshot.account_id == batch.account_id,
                AccountMetricSnapshot.stat_date == key.stat_date,
            )
            .order_by(
                AccountMetricSnapshot.updated_at.desc(),
                AccountMetricSnapshot.id.desc(),
            )
        )
    )
    if not winners:
        for snapshot in snapshots:
            await session.delete(snapshot)
        return
    snapshot = snapshots[0] if snapshots else AccountMetricSnapshot(
        org_id=batch.org_id,
        account_id=batch.account_id,
        import_batch_id=newest_winner(winners).observation.import_batch_id,
        source_kind=newest_winner(winners).observation.source_kind,
        stat_date=key.stat_date,
    )
    if not snapshots:
        session.add(snapshot)
    for duplicate in snapshots[1:]:
        await session.delete(duplicate)
    for field_name in (
        "follower_count",
        "follower_delta",
        "total_play",
        "total_exposure",
        "engagement_rate",
        "profile_visit_count",
        "unfollow_count",
        "like_count",
        "comment_count",
        "share_count",
        "cover_click_rate",
    ):
        setattr(snapshot, field_name, None)
    for field_name, winner in winners.items():
        if hasattr(snapshot, field_name):
            setattr(snapshot, field_name, winner.value)
    provenance = newest_winner(winners)
    snapshot.import_batch_id = provenance.observation.import_batch_id
    snapshot.source_kind = provenance.observation.source_kind


async def _rebuild_content_metric_projection(
    *,
    session: AsyncSession,
    batch: DataImportBatch,
    key: ProjectionKey,
    winners,
) -> None:
    content_id = _content_id_from_projection_key(batch.account_id, key.entity_key)
    snapshots = list(
        await session.scalars(
            select(MetricSnapshot)
            .where(
                MetricSnapshot.org_id == batch.org_id,
                MetricSnapshot.account_id == batch.account_id,
                MetricSnapshot.platform_content_record_id == content_id,
                MetricSnapshot.stat_date == key.stat_date,
                MetricSnapshot.import_batch_id.is_not(None),
            )
            .order_by(MetricSnapshot.updated_at.desc(), MetricSnapshot.id.desc())
        )
    )
    if not winners:
        for snapshot in snapshots:
            await session.delete(snapshot)
        await _reassign_or_delete_content_record(
            session=session,
            batch=batch,
            content_id=content_id,
            entity_key=key.entity_key,
        )
        return
    provenance = newest_winner(winners)
    snapshot = snapshots[0] if snapshots else MetricSnapshot(
        org_id=batch.org_id,
        account_id=batch.account_id,
        import_batch_id=provenance.observation.import_batch_id,
        platform_content_record_id=content_id,
        source=MetricSource(_batch_platform(batch).value),
        stat_date=key.stat_date,
        play=0,
        exposure=0,
        completion_rate=0.0,
        like_rate=0.0,
        comment_rate=0.0,
        share_rate=0.0,
        follower_delta=0,
    )
    if not snapshots:
        session.add(snapshot)
    for duplicate in snapshots[1:]:
        await session.delete(duplicate)
    nullable_fields = (
        "title",
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
    for field_name in nullable_fields:
        setattr(snapshot, field_name, None)
    for field_name in (
        "play",
        "exposure",
        "completion_rate",
        "follower_delta",
    ):
        setattr(snapshot, field_name, 0)
    for field_name, winner in winners.items():
        if hasattr(snapshot, field_name):
            setattr(snapshot, field_name, winner.value)
    snapshot.import_batch_id = provenance.observation.import_batch_id
    snapshot.like_rate = _derived_rate(
        numerator=snapshot.like_count,
        denominator=snapshot.play,
    )
    snapshot.comment_rate = _derived_rate(
        numerator=snapshot.comment_count,
        denominator=snapshot.play,
    )
    snapshot.share_rate = _derived_rate(
        numerator=snapshot.share_count,
        denominator=snapshot.play,
    )
    await _reassign_or_delete_content_record(
        session=session,
        batch=batch,
        content_id=content_id,
        entity_key=key.entity_key,
    )


async def _rebuild_audience_projection(
    *,
    session: AsyncSession,
    batch: DataImportBatch,
    key: ProjectionKey,
    winners,
) -> None:
    dimension = _audience_dimension_from_projection_key(batch.account_id, key.entity_key)
    snapshots = list(
        await session.scalars(
            select(AudienceProfileSnapshot)
            .options(selectinload(AudienceProfileSnapshot.items))
            .where(
                AudienceProfileSnapshot.org_id == batch.org_id,
                AudienceProfileSnapshot.account_id == batch.account_id,
                AudienceProfileSnapshot.stat_date == key.stat_date,
                AudienceProfileSnapshot.dimension == dimension,
            )
            .order_by(
                AudienceProfileSnapshot.updated_at.desc(),
                AudienceProfileSnapshot.id.desc(),
            )
        )
    )
    if not winners:
        for snapshot in snapshots:
            await session.delete(snapshot)
        return
    provenance = newest_winner(winners)
    snapshot = snapshots[0] if snapshots else AudienceProfileSnapshot(
        org_id=batch.org_id,
        account_id=batch.account_id,
        import_batch_id=provenance.observation.import_batch_id,
        source_kind=provenance.observation.source_kind,
        stat_date=key.stat_date,
        dimension=dimension,
        items=[],
    )
    if not snapshots:
        session.add(snapshot)
        await session.flush()
    for duplicate in snapshots[1:]:
        await session.delete(duplicate)
    snapshot.total_audience = None
    snapshot.items = []
    if "total_audience" in winners:
        snapshot.total_audience = _int_or_none(winners["total_audience"].value)
    if "items" in winners:
        snapshot.items = [
            AudienceProfileItem(
                org_id=batch.org_id,
                account_id=batch.account_id,
                snapshot_id=snapshot.id,
                label=str(item["label"]),
                value=str(item["value"]),
                ratio=_float_or_none(item.get("ratio")),
                rank=rank,
                meta={},
            )
            for rank, item in enumerate(winners["items"].value, start=1)
        ]
    snapshot.import_batch_id = provenance.observation.import_batch_id
    snapshot.source_kind = provenance.observation.source_kind


async def _rebuild_benchmark_projection(
    *,
    session: AsyncSession,
    batch: DataImportBatch,
    key: ProjectionKey,
    winners,
) -> None:
    benchmark_code, metric_code = _benchmark_parts_from_projection_key(
        batch.account_id,
        key.entity_key,
    )
    snapshots = list(
        await session.scalars(
            select(BenchmarkSnapshot)
            .where(
                BenchmarkSnapshot.org_id == batch.org_id,
                BenchmarkSnapshot.account_id == batch.account_id,
                BenchmarkSnapshot.stat_date == key.stat_date,
                BenchmarkSnapshot.benchmark_code == benchmark_code,
                BenchmarkSnapshot.metric_code == metric_code,
            )
            .order_by(BenchmarkSnapshot.updated_at.desc(), BenchmarkSnapshot.id.desc())
        )
    )
    if not winners:
        for snapshot in snapshots:
            await session.delete(snapshot)
        return
    provenance = newest_winner(winners)
    snapshot = snapshots[0] if snapshots else BenchmarkSnapshot(
        org_id=batch.org_id,
        account_id=batch.account_id,
        import_batch_id=provenance.observation.import_batch_id,
        source_kind=provenance.observation.source_kind,
        stat_date=key.stat_date,
        benchmark_code=benchmark_code,
        metric_code=metric_code,
    )
    if not snapshots:
        session.add(snapshot)
    for duplicate in snapshots[1:]:
        await session.delete(duplicate)
    snapshot.metric_value = None
    snapshot.sample_size = None
    snapshot.meta = {}
    if "metric_value" in winners:
        snapshot.metric_value = _float_or_none(winners["metric_value"].value)
    if "sample_size" in winners:
        snapshot.sample_size = _int_or_none(winners["sample_size"].value)
    if "meta" in winners:
        snapshot.meta = dict(winners["meta"].value)
    snapshot.import_batch_id = provenance.observation.import_batch_id
    snapshot.source_kind = provenance.observation.source_kind


def _content_id_from_projection_key(account_id: int, entity_key: str) -> int:
    prefix = f"account:{account_id}:content:"
    if not entity_key.startswith(prefix):
        raise ValueError(f"invalid content projection key: {entity_key}")
    return int(entity_key.removeprefix(prefix))


async def _reassign_or_delete_content_record(
    *,
    session: AsyncSession,
    batch: DataImportBatch,
    content_id: int,
    entity_key: str,
) -> None:
    content = await session.get(PlatformContentRecord, content_id)
    if content is None or content.canonical_import_batch_id != batch.id:
        return
    surviving = await session.scalar(
        select(DataFieldObservation)
        .where(
            DataFieldObservation.org_id == batch.org_id,
            DataFieldObservation.account_id == batch.account_id,
            DataFieldObservation.domain == "content_metrics",
            DataFieldObservation.entity_key == entity_key,
            DataFieldObservation.active.is_(True),
        )
        .order_by(
            DataFieldObservation.source_priority.desc(),
            DataFieldObservation.confirmed_sequence.desc(),
            DataFieldObservation.id.desc(),
        )
    )
    if surviving is None:
        await session.delete(content)
        return
    source_row = await session.get(DataImportRow, surviving.import_row_id)
    content.canonical_import_batch_id = surviving.import_batch_id
    content.canonical_import_row_number = (
        source_row.row_number if source_row is not None else None
    )
    content.source_kind = surviving.source_kind
    content.source_metadata = {
        **(content.source_metadata or {}),
        "import_batch_id": surviving.import_batch_id,
        "row_number": content.canonical_import_row_number,
    }


def _audience_dimension_from_projection_key(account_id: int, entity_key: str) -> str:
    prefix = f"account:{account_id}:audience:"
    suffix = ":__profile__"
    if not entity_key.startswith(prefix) or not entity_key.endswith(suffix):
        raise ValueError(f"invalid audience projection key: {entity_key}")
    return unquote(entity_key.removeprefix(prefix).removesuffix(suffix))


def _benchmark_parts_from_projection_key(
    account_id: int,
    entity_key: str,
) -> tuple[str, str]:
    prefix = f"account:{account_id}:benchmark:"
    marker = ":metric:"
    if not entity_key.startswith(prefix) or marker not in entity_key:
        raise ValueError(f"invalid benchmark projection key: {entity_key}")
    benchmark_code, metric_code = entity_key.removeprefix(prefix).split(marker, 1)
    return unquote(benchmark_code), unquote(metric_code)


async def _project_content_record(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> tuple[PlatformContentRecord, list[dict[str, object]]]:
    content_record, action = await _ensure_platform_content_record(
        session=session,
        batch=batch,
        row=row,
    )
    if row.normalized_values.get("content_format") is not None:
        content_record.content_format = _string_or_none(
            row.normalized_values.get("content_format")
        )
    if row.normalized_values.get("review_status") is not None:
        content_record.review_status = _string_or_none(
            row.normalized_values.get("review_status")
        )
    return content_record, [
        {
            "kind": "platform_content_record",
            "id": content_record.id,
            "action": action,
        }
    ]


async def _upsert_metric_snapshot(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
    content_record: PlatformContentRecord,
) -> tuple[MetricSnapshot, str]:
    stat_date = _row_stat_date(batch=batch, row=row)
    entity_key = content_entity_key(batch.account_id, content_record.id)
    merge_values = {
        field_name: row.normalized_values.get(field_name)
        for field_name in (
            "title",
            "play",
            "exposure",
            "completion_rate",
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
        if field_name in row.normalized_values
    }
    winners = await record_and_resolve_fields(
        session,
        batch=batch,
        row=row,
        domain="content_metrics",
        entity_key=entity_key,
        stat_date=stat_date,
        values=merge_values,
    )
    existing = await session.scalar(
        select(MetricSnapshot)
        .where(
            MetricSnapshot.org_id == batch.org_id,
            MetricSnapshot.account_id == batch.account_id,
            MetricSnapshot.import_batch_id.is_not(None),
            MetricSnapshot.platform_content_record_id == content_record.id,
            MetricSnapshot.stat_date == stat_date,
        )
        .order_by(MetricSnapshot.updated_at.desc(), MetricSnapshot.id.desc())
    )
    action = "updated" if existing is not None else "created"
    metric = existing or MetricSnapshot(
        org_id=batch.org_id,
        account_id=batch.account_id,
        import_batch_id=batch.id,
        platform_content_record_id=content_record.id,
        source=MetricSource(_batch_platform(batch).value),
        stat_date=stat_date,
        play=0,
        exposure=0,
        completion_rate=0.0,
        like_rate=0.0,
        comment_rate=0.0,
        share_rate=0.0,
        follower_delta=0,
    )
    for field_name, winner in winners.items():
        if hasattr(metric, field_name):
            setattr(metric, field_name, winner.value)
    provenance = newest_winner(winners)
    if provenance is not None:
        metric.import_batch_id = provenance.observation.import_batch_id
    metric.like_rate = _derived_rate(
        numerator=metric.like_count,
        denominator=metric.play,
    )
    metric.comment_rate = _derived_rate(
        numerator=metric.comment_count,
        denominator=metric.play,
    )
    metric.share_rate = _derived_rate(
        numerator=metric.share_count,
        denominator=metric.play,
    )
    if existing is None:
        session.add(metric)
    await session.flush()
    return metric, action


async def _upsert_account_metric_snapshot(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> tuple[AccountMetricSnapshot, str]:
    stat_date = _row_stat_date(batch=batch, row=row)
    merge_values: dict[str, object | None] = {}
    if "total_play" in row.normalized_values:
        merge_values["total_play"] = row.normalized_values.get("total_play")
    elif "play" in row.normalized_values:
        merge_values["total_play"] = row.normalized_values.get("play")
    if "total_exposure" in row.normalized_values:
        merge_values["total_exposure"] = row.normalized_values.get("total_exposure")
    elif "exposure" in row.normalized_values:
        merge_values["total_exposure"] = row.normalized_values.get("exposure")
    for field_name in (
        "follower_count",
        "follower_delta",
        "engagement_rate",
        "profile_visit_count",
        "unfollow_count",
        "like_count",
        "comment_count",
        "share_count",
        "cover_click_rate",
    ):
        if field_name in row.normalized_values:
            merge_values[field_name] = row.normalized_values.get(field_name)
    winners = await record_and_resolve_fields(
        session,
        batch=batch,
        row=row,
        domain="account_metrics",
        entity_key=account_entity_key(batch.account_id),
        stat_date=stat_date,
        values=merge_values,
    )
    existing = await session.scalar(
        select(AccountMetricSnapshot)
        .where(
            AccountMetricSnapshot.org_id == batch.org_id,
            AccountMetricSnapshot.account_id == batch.account_id,
            AccountMetricSnapshot.stat_date == stat_date,
        )
        .order_by(
            AccountMetricSnapshot.updated_at.desc(),
            AccountMetricSnapshot.id.desc(),
        )
    )
    action = "updated" if existing is not None else "created"
    snapshot = existing or AccountMetricSnapshot(
        org_id=batch.org_id,
        account_id=batch.account_id,
        import_batch_id=batch.id,
        source_kind=batch.source_kind,
        stat_date=stat_date,
    )
    for field_name, winner in winners.items():
        if hasattr(snapshot, field_name):
            setattr(snapshot, field_name, winner.value)
    provenance = newest_winner(winners)
    if provenance is not None:
        snapshot.import_batch_id = provenance.observation.import_batch_id
        snapshot.source_kind = provenance.observation.source_kind
    if existing is None:
        session.add(snapshot)
    await session.flush()
    return snapshot, action


async def _upsert_benchmark_snapshot(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
    metric_code: str,
    metric_value: float | None,
    benchmark_code: str | None = None,
    sample_size: int | None = None,
) -> tuple[BenchmarkSnapshot, str]:
    stat_date = _row_stat_date(batch=batch, row=row)
    benchmark_code = benchmark_code or _benchmark_code(row=row)
    resolved_sample_size = sample_size
    if resolved_sample_size is None and "publish_count" in row.normalized_values:
        resolved_sample_size = _int_or_none(row.normalized_values.get("publish_count"))
    meta = {
        "content_format": row.normalized_values.get("content_format"),
        "vertical": row.normalized_values.get("vertical"),
        "period_start": _date_or_none(row.normalized_values.get("period_start")),
        "period_end": _date_or_none(row.normalized_values.get("period_end")),
    }
    merge_values: dict[str, object | None] = {
        "metric_value": metric_value,
        "sample_size": resolved_sample_size,
    }
    if any(value is not None for value in meta.values()):
        merge_values["meta"] = meta
    entity_key = (
        f"{benchmark_entity_key(batch.account_id, benchmark_code)}:"
        f"metric:{quote(metric_code, safe='')}"
    )
    winners = await record_and_resolve_fields(
        session,
        batch=batch,
        row=row,
        domain="benchmarks",
        entity_key=entity_key,
        stat_date=stat_date,
        values=merge_values,
    )
    existing = await session.scalar(
        select(BenchmarkSnapshot)
        .where(
            BenchmarkSnapshot.org_id == batch.org_id,
            BenchmarkSnapshot.account_id == batch.account_id,
            BenchmarkSnapshot.stat_date == stat_date,
            BenchmarkSnapshot.benchmark_code == benchmark_code,
            BenchmarkSnapshot.metric_code == metric_code,
        )
        .order_by(BenchmarkSnapshot.updated_at.desc(), BenchmarkSnapshot.id.desc())
    )
    action = "updated" if existing is not None else "created"
    snapshot = existing or BenchmarkSnapshot(
        org_id=batch.org_id,
        account_id=batch.account_id,
        import_batch_id=batch.id,
        source_kind=batch.source_kind,
        stat_date=stat_date,
        benchmark_code=benchmark_code,
        metric_code=metric_code,
    )
    if "metric_value" in winners:
        snapshot.metric_value = _float_or_none(winners["metric_value"].value)
    if "sample_size" in winners:
        snapshot.sample_size = _int_or_none(winners["sample_size"].value)
    if "meta" in winners:
        snapshot.meta = dict(winners["meta"].value)
    provenance = newest_winner(winners)
    if provenance is not None:
        snapshot.import_batch_id = provenance.observation.import_batch_id
        snapshot.source_kind = provenance.observation.source_kind
    if existing is None:
        session.add(snapshot)
    await session.flush()
    return snapshot, action


async def _projected_domains_for_batch(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
) -> set[str]:
    projected_domains: set[str] = set()
    if await session.scalar(
        select(AccountMetricSnapshot.id).where(AccountMetricSnapshot.import_batch_id == batch.id)
    ):
        projected_domains.add("account_metrics")
    if await session.scalar(
        select(BenchmarkSnapshot.id).where(BenchmarkSnapshot.import_batch_id == batch.id)
    ):
        projected_domains.add("benchmarks")
    if await session.scalar(
        select(AudienceProfileSnapshot.id).where(
            AudienceProfileSnapshot.import_batch_id == batch.id
        )
    ):
        projected_domains.add("audience_profiles")
    if await session.scalar(
        select(PlatformContentRecord.id).where(
            PlatformContentRecord.canonical_import_batch_id == batch.id
        )
    ) or await session.scalar(
        select(MetricSnapshot.id).where(MetricSnapshot.import_batch_id == batch.id)
    ):
        projected_domains.add("content_metrics")
    return projected_domains


def _batch_platform(batch: DataImportBatch):
    return {
        "douyin_work_list_v1": Platform.DOUYIN,
        "douyin_single_content_v1": Platform.DOUYIN,
        "douyin_daily_play_v1": Platform.DOUYIN,
        "douyin_period_aggregate_v1": Platform.DOUYIN,
    }.get(batch.template_code, Platform.DOUYIN)


def _row_stat_date(*, batch: DataImportBatch, row: DataImportRow):
    stat_date = row.normalized_values.get("stat_date")
    if isinstance(stat_date, str):
        return date.fromisoformat(stat_date)
    if isinstance(stat_date, date):
        return stat_date
    published_at = row.normalized_values.get("published_at")
    if isinstance(published_at, str):
        published_at = datetime.fromisoformat(published_at)
    if isinstance(published_at, datetime):
        return published_at.date()
    if batch.period_end is not None:
        return batch.period_end
    if batch.period_start is not None:
        return batch.period_start
    return datetime.now(UTC).date()


def _projection_gap(*, row: DataImportRow, missing_fields: list[str]) -> dict[str, object] | None:
    missing = [field for field in missing_fields if field in row.normalized_values]
    if not missing:
        return None
    return {
        "kind": "projection_gap",
        "missing_fields": missing,
        "report": "staging_only",
    }


def _derived_rate(*, numerator, denominator) -> float:
    numerator_value = _int_or_none(numerator)
    denominator_value = _int_or_none(denominator)
    if numerator_value is None or denominator_value in (None, 0):
        return 0.0
    return numerator_value / denominator_value


def _float_or_zero(value) -> float:
    return 0.0 if value is None else float(value)


def _benchmark_code(*, row: DataImportRow) -> str:
    content_format = str(row.normalized_values.get("content_format") or "unknown")
    vertical = str(row.normalized_values.get("vertical") or "unknown")
    return f"period_aggregate:{content_format}:{vertical}"


def _date_or_none(value) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _dialect_name(session: AsyncSession) -> str:
    bind = session.get_bind()
    return bind.dialect.name if bind is not None else ""


def _int_or_none(value) -> int | None:
    return None if value is None else int(value)


def _float_or_none(value) -> float | None:
    return None if value is None else float(value)


def _string_or_none(value) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _resolved_replay_or_error(
    *,
    row: DataImportRow,
    conflict: DataConflict | None,
    resolution: RowMatchResolution,
) -> DataImportRow:
    if row.resolution_outcome == "confirmed" and conflict is not None:
        if resolution.confirmed is True and resolution.selected_content_id is None:
            return row
        raise ValueError("import row has already resolved to a different terminal outcome")
    if row.resolution_outcome in {"matched", "no_match"} and conflict is not None:
        replay_matches = (
            row.resolution_outcome == "no_match"
            and resolution.selected_content_id is None
        ) or (
            row.resolution_outcome == "matched"
            and row.platform_content_record_id == resolution.selected_content_id
        )
        if replay_matches:
            return row
        raise ValueError("import row has already resolved to a different terminal outcome")
    raise ValueError("import row must be in needs_resolution with an open conflict")


def _resolve_adapter(source: SourceInput) -> DataSourceAdapter:
    for adapter in REGISTERED_ADAPTERS:
        detection = adapter.detect(source)
        if detection.matched:
            return adapter
    raise ValueError("No data import adapter matched the provided source")


def _validated_extension(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Unsupported preview artifact extension")
    return extension


def _validated_screenshot(filename: str, content: bytes) -> tuple[str, str]:
    if len(content) > 5 * 1024 * 1024:
        raise ValueError("Screenshot image exceeds the 5 MB limit")
    extension = Path(filename).suffix.lower()
    detected: tuple[str, str] | None = None
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = (".png", "image/png")
    elif content.startswith(b"\xff\xd8\xff"):
        detected = (".jpg", "image/jpeg")
    elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        detected = (".webp", "image/webp")
    if detected is None:
        raise ValueError("Screenshot must contain a valid PNG, JPEG, or WebP image")
    allowed_extensions = {".jpg", ".jpeg"} if detected[0] == ".jpg" else {detected[0]}
    if extension not in allowed_extensions:
        raise ValueError("Screenshot extension does not match the image content")
    return detected


def _sanitize_filename(filename: str, *, extension: str) -> str:
    candidate = PurePath(filename).name or f"upload{extension}"
    stem = Path(candidate).stem or "upload"
    max_stem_length = 255 - len(extension)
    return f"{stem[:max_stem_length]}{extension}"


def _content_type_for_extension(extension: str) -> str:
    return {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv",
    }[extension]


def _build_storage_key(
    *,
    org_id: int,
    account_id: int,
    batch_id: int,
    sha256: str,
    extension: str,
) -> str:
    return f"account-data/{org_id}/{account_id}/{batch_id}/{sha256}{extension}"


def _write_artifact_atomic(storage_key: str, content: bytes) -> None:
    target = storage.resolve(storage_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temp_path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _delete_artifact(storage_key: str) -> None:
    target = storage.resolve(storage_key)
    if target.exists():
        target.unlink()


def _quarantine_artifacts(storage_keys: list[str]) -> list[QuarantinedArtifact]:
    moved: list[QuarantinedArtifact] = []
    try:
        for storage_key in storage_keys:
            original = storage.resolve(storage_key)
            if not original.exists():
                continue
            quarantined = original.with_name(f".{original.name}.{uuid4().hex}.deleting")
            os.replace(original, quarantined)
            moved.append(QuarantinedArtifact(original=original, quarantined=quarantined))
    except Exception:
        _restore_quarantined_artifacts(moved)
        raise
    return moved


def _restore_quarantined_artifacts(items: list[QuarantinedArtifact]) -> None:
    for item in reversed(items):
        if item.quarantined.exists():
            item.original.parent.mkdir(parents=True, exist_ok=True)
            os.replace(item.quarantined, item.original)


def _purge_quarantined_artifacts(items: list[QuarantinedArtifact]) -> None:
    for item in items:
        item.quarantined.unlink(missing_ok=True)


def _derive_period_boundary(rows: list, *, field_name: str, reducer):
    values: list[date] = []
    for row in rows:
        value = row.normalized.get(field_name)
        if isinstance(value, datetime):
            values.append(value.date())
        elif isinstance(value, date):
            values.append(value)
    return reducer(values) if values else None


def _derive_effective_period_boundary(rows: list, *, field_name: str, reducer):
    return _derive_period_boundary(
        rows,
        field_name=field_name,
        reducer=reducer,
    ) or _derive_period_boundary(
        rows,
        field_name="stat_date",
        reducer=reducer,
    )


def _manual_normalized_values(payload: dict) -> dict:
    values = {
        "data_domain": payload["data_domain"],
        "stat_date": payload["stat_date"],
        "period_start": payload.get("period_start"),
        "period_end": payload.get("period_end"),
    }
    if payload["data_domain"] == "account_period_totals":
        values.update(payload.get("account_metrics") or {})
    elif payload["data_domain"] == "audience_dimension":
        values.update(
            {
                "dimension": payload.get("dimension"),
                "total_audience": payload.get("total_audience"),
                "audience_items": payload.get("audience_items") or [],
            }
        )
    else:
        values.update(
            {
                "benchmark_code": payload.get("benchmark_code"),
                "benchmark_metrics": payload.get("benchmark_metrics") or [],
            }
        )
    return _json_ready(values)


def _coerce_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    return None


def _issue_to_dict(issue: RowIssue) -> dict[str, str | None]:
    return {
        "code": issue.code,
        "message": issue.message,
        "field": issue.field,
    }


def _json_ready(value):
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _assert_org_account_scope(*, user: User, account: Account) -> None:
    if user.org_id != account.org_id:
        raise ValueError("user and account must belong to the same organization")


def _assert_batch_scope(*, batch: DataImportBatch, user: User) -> None:
    if batch.org_id != user.org_id:
        raise ValueError("resolved_by user must belong to the same organization as the batch")
