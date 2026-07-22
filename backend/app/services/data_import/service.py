from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePath
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import storage
from app.models import (
    Account,
    DataArtifact,
    DataConflict,
    DataImportBatch,
    DataImportRow,
    MetricSnapshot,
    PlatformContentRecord,
    User,
)
from app.models.enums import (
    ConflictStatus,
    ContentIdentityConfidence,
    ImportBatchStatus,
    ImportRowStatus,
    MetricSource,
    Platform,
)
from app.services.data_import import REGISTERED_ADAPTERS
from app.services.data_import.identity import build_weak_fingerprint, match_content
from app.services.data_import.parser import SUPPORTED_EXTENSIONS, RowIssue
from app.services.data_import.templates import KNOWN_TEMPLATES


class DataImportBatchNotFoundError(LookupError):
    pass


class DataImportCommitConflictError(RuntimeError):
    pass


class DataImportStateError(RuntimeError):
    pass


class DataImportRevokeConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RowMatchResolution:
    selected_content_id: int | None
    resolved_by: User


@dataclass(frozen=True, slots=True)
class PreviewRecoveryResult:
    batch: DataImportBatch | None = None
    wrote_storage_key: str | None = None
    retired_stale_batch: bool = False


async def create_preview(
    session: AsyncSession,
    *,
    user: User,
    account: Account,
    filename: str,
    content: bytes,
) -> DataImportBatch:
    _assert_org_account_scope(user=user, account=account)
    source = {"filename": filename, "data": content}
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
) -> list[DataImportBatch]:
    rows = await session.scalars(
        select(DataImportBatch)
        .options(selectinload(DataImportBatch.artifacts))
        .where(
            DataImportBatch.org_id == org_id,
            DataImportBatch.account_id == account_id,
        )
        .order_by(DataImportBatch.id.desc())
    )
    return list(rows)


async def account_status_summary(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
) -> dict[str, object]:
    batches = await session.scalars(
        select(DataImportBatch)
        .where(
            DataImportBatch.org_id == org_id,
            DataImportBatch.account_id == account_id,
            DataImportBatch.committed_at.is_not(None),
            DataImportBatch.revoked_at.is_(None),
        )
        .order_by(DataImportBatch.committed_at.desc(), DataImportBatch.id.desc())
    )
    latest_confirmed_at = None
    coverage = {
        "account_metrics": "missing",
        "content_metrics": "missing",
        "benchmarks": "missing",
    }
    sources: list[dict[str, object]] = []
    seen_domains: set[str] = set()
    template_domains = {item.code: item.data_domain for item in KNOWN_TEMPLATES}
    for batch in batches:
        if latest_confirmed_at is None:
            latest_confirmed_at = batch.committed_at
        data_domain = template_domains.get(batch.template_code, "unknown")
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
    return {
        "account_id": account_id,
        "latest_confirmed_at": latest_confirmed_at,
        "coverage": coverage,
        "sources": sources,
    }


async def commit_batch(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    batch_id: int,
    actor: User,
) -> DataImportBatch:
    batch = await load_scoped_batch(
        session,
        org_id=org_id,
        account_id=account_id,
        batch_id=batch_id,
    )
    _assert_batch_scope(batch=batch, user=actor)
    if batch.revoked_at is not None or batch.status is ImportBatchStatus.REVOKED:
        raise DataImportStateError("revoked batches cannot be committed")
    if batch.committed_at is not None or batch.status is ImportBatchStatus.COMMITTED:
        return batch

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
        for row in batch.rows:
            row.projected_target_ids = await _project_row_targets(
                session=session,
                batch=batch,
                row=row,
            )
            row.status = ImportRowStatus.COMMITTED
        batch.status = ImportBatchStatus.COMMITTED
        batch.committed_at = datetime.now(UTC)
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
    batch = await load_scoped_batch(
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

    conflicts = await _find_revoke_conflicts(session=session, batch=batch)
    if conflicts:
        await _record_revoke_conflicts(session=session, batch=batch, conflicts=conflicts)
        raise DataImportRevokeConflictError("batch contains superseded projections")

    try:
        for row in batch.rows:
            await _delete_row_targets(session=session, batch=batch, row=row)
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
            row_count=preview_row_count,
            period_start=_derive_period_boundary(rows, field_name="period_start", reducer=min),
            period_end=_derive_period_boundary(rows, field_name="period_end", reducer=max),
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
        DataConflict.field_name == "platform_content_record_id",
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        row_query = row_query.with_for_update()
        conflict_query = conflict_query.with_for_update()
    row = await session.scalar(row_query)
    conflict = await session.scalar(conflict_query)
    return row, conflict


async def _project_row_targets(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
    row: DataImportRow,
) -> list[dict[str, object]]:
    content_record, content_action = await _ensure_platform_content_record(
        session=session,
        batch=batch,
        row=row,
    )
    metric = MetricSnapshot(
        org_id=batch.org_id,
        account_id=batch.account_id,
        import_batch_id=batch.id,
        platform_content_record_id=content_record.id,
        source=MetricSource(_batch_platform(batch).value),
        stat_date=_row_stat_date(batch=batch, row=row),
        title=row.normalized_values.get("title"),
        play=int(row.normalized_values.get("play") or 0),
        exposure=int(row.normalized_values.get("exposure") or 0),
        completion_rate=float(row.normalized_values.get("completion_rate") or 0.0),
        like_rate=float(row.normalized_values.get("like_rate") or 0.0),
        comment_rate=float(row.normalized_values.get("comment_rate") or 0.0),
        share_rate=float(row.normalized_values.get("share_rate") or 0.0),
        follower_delta=int(row.normalized_values.get("follower_delta") or 0),
        like_count=_int_or_none(row.normalized_values.get("like_count")),
        comment_count=_int_or_none(row.normalized_values.get("comment_count")),
        share_count=_int_or_none(row.normalized_values.get("share_count")),
        favorite_count=_int_or_none(row.normalized_values.get("favorite_count")),
        cover_click_rate=_float_or_none(row.normalized_values.get("cover_click_rate")),
        avg_watch_time_seconds=_float_or_none(
            row.normalized_values.get("avg_watch_time_seconds")
        ),
    )
    session.add(metric)
    await session.flush()
    row.platform_content_record_id = content_record.id
    return [
        {
            "kind": "platform_content_record",
            "id": content_record.id,
            "action": content_action,
        },
        {
            "kind": "metric_snapshot",
            "id": metric.id,
            "action": "created",
        },
    ]


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
        canonical_import_batch_id=batch.id,
        title=row.normalized_values.get("title"),
        published_at=published_at,
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
        weak_fingerprint=weak_fingerprint,
    )
    session.add(record)
    await session.flush()
    return record, "created"


async def _find_revoke_conflicts(
    session: AsyncSession,
    *,
    batch: DataImportBatch,
) -> list[dict[str, object]]:
    conflicts: list[dict[str, object]] = []
    for row in batch.rows:
        for target in row.projected_target_ids:
            if target.get("kind") != "platform_content_record":
                continue
            if target.get("action") != "created":
                continue
            content = await session.get(PlatformContentRecord, int(target["id"]))
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
    conflicts: list[dict[str, object]],
) -> None:
    timestamp = datetime.now(UTC)
    for item in conflicts:
        existing = await session.scalar(
            select(DataConflict).where(
                DataConflict.org_id == batch.org_id,
                DataConflict.account_id == batch.account_id,
                DataConflict.batch_id == batch.id,
                DataConflict.row_number == int(item["row_number"]),
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
                row_number=int(item["row_number"]),
                status=ConflictStatus.OPEN,
                field_name="projected_target_ids",
                conflict_code="superseded_projection",
                message=str(item["message"]),
                incoming_value={"batch_id": batch.id, "detected_at": timestamp.isoformat()},
            )
        )
    await session.commit()


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
        elif target.get("kind") == "platform_content_record" and target.get("action") == "created":
            content = await session.get(PlatformContentRecord, int(target["id"]))
            if content is not None and content.canonical_import_batch_id == batch.id:
                await session.delete(content)


def _batch_platform(batch: DataImportBatch):
    return {
        "douyin_work_list_v1": Platform.DOUYIN,
        "douyin_single_content_v1": Platform.DOUYIN,
        "douyin_daily_play_v1": Platform.DOUYIN,
        "douyin_period_aggregate_v1": Platform.DOUYIN,
    }.get(batch.template_code, Platform.DOUYIN)


def _row_stat_date(*, batch: DataImportBatch, row: DataImportRow):
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


def _int_or_none(value) -> int | None:
    return None if value is None else int(value)


def _float_or_none(value) -> float | None:
    return None if value is None else float(value)


def _resolved_replay_or_error(
    *,
    row: DataImportRow,
    conflict: DataConflict | None,
    resolution: RowMatchResolution,
) -> DataImportRow:
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


def _resolve_adapter(source) -> object:
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


def _sanitize_filename(filename: str, *, extension: str) -> str:
    candidate = PurePath(filename).name or f"upload{extension}"
    stem = Path(candidate).stem or "upload"
    return f"{stem}{extension}"


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


def _derive_period_boundary(rows: list, *, field_name: str, reducer):
    values: list[date] = []
    for row in rows:
        value = row.normalized.get(field_name)
        if isinstance(value, datetime):
            values.append(value.date())
        elif isinstance(value, date):
            values.append(value)
    return reducer(values) if values else None


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
