from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePath
from uuid import uuid4

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import storage
from app.models import (
    Account,
    DataArtifact,
    DataConflict,
    DataImportBatch,
    DataImportRow,
    PlatformContentRecord,
    User,
)
from app.models.enums import (
    ConflictStatus,
    ContentIdentityConfidence,
    ImportBatchStatus,
    ImportRowStatus,
)
from app.services.data_import import REGISTERED_ADAPTERS
from app.services.data_import.identity import build_weak_fingerprint, match_content
from app.services.data_import.parser import SUPPORTED_EXTENSIONS, RowIssue


@dataclass(frozen=True, slots=True)
class RowMatchResolution:
    selected_content_id: int | None
    resolved_by: User


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
    sha256 = hashlib.sha256(content).hexdigest()

    existing = await _find_existing_preview(
        session,
        org_id=account.org_id,
        account_id=account.id,
        source_kind=adapter.source_kind,
        template_code=parsed.template_code,
        sha256=sha256,
    )
    if existing is not None:
        return existing

    extension = _validated_extension(filename)
    batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=user.id,
        source_kind=adapter.source_kind,
        status=ImportBatchStatus.PREVIEW_READY,
        template_code=parsed.template_code,
        row_count=preview.total_rows,
        period_start=_derive_period_boundary(rows, field_name="period_start", reducer=min),
        period_end=_derive_period_boundary(rows, field_name="period_end", reducer=max),
    )
    session.add(batch)
    await session.flush()

    storage_key = _build_storage_key(
        org_id=account.org_id,
        account_id=account.id,
        batch_id=batch.id,
        sha256=sha256,
        extension=extension,
    )
    artifact = DataArtifact(
        org_id=account.org_id,
        account_id=account.id,
        batch_id=batch.id,
        filename=_sanitize_filename(filename, extension=extension),
        content_type=_content_type_for_extension(extension),
        byte_size=len(content),
        sha256=sha256,
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

    wrote_file = False
    try:
        _write_artifact_atomic(storage_key, content)
        wrote_file = True
        await session.commit()
    except Exception:
        await session.rollback()
        if wrote_file:
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
    row = await session.scalar(
        select(DataImportRow)
        .where(
            DataImportRow.org_id == batch.org_id,
            DataImportRow.account_id == batch.account_id,
            DataImportRow.batch_id == batch.id,
            DataImportRow.row_number == row_number,
        )
        .options(selectinload(DataImportRow.batch))
    )
    if row is None:
        raise ValueError(f"Import row {row_number} does not exist in batch {batch.id}")
    if row.status is ImportRowStatus.INVALID:
        raise ValueError("Invalid rows cannot be resolved")

    selected_content_id = resolution.selected_content_id
    if selected_content_id is not None:
        if row.candidate_content_ids and selected_content_id not in row.candidate_content_ids:
            raise ValueError("selected candidate is not available for this import row")
        candidate = await session.get(PlatformContentRecord, selected_content_id)
        if (
            candidate is None
            or candidate.org_id != batch.org_id
            or candidate.account_id != batch.account_id
        ):
            raise ValueError("selected candidate is outside the batch account scope")
    if (
        row.platform_content_record_id is not None
        and row.platform_content_record_id != selected_content_id
        and row.status is ImportRowStatus.READY
    ):
        raise ValueError("import row has already been resolved with a different candidate")

    conflict = await session.scalar(
        select(DataConflict).where(
            DataConflict.org_id == batch.org_id,
            DataConflict.account_id == batch.account_id,
            DataConflict.batch_id == batch.id,
            DataConflict.row_number == row_number,
            DataConflict.field_name == "platform_content_record_id",
        )
    )
    row.platform_content_record_id = selected_content_id
    row.status = ImportRowStatus.READY
    if conflict is not None and conflict.status is ConflictStatus.OPEN:
        conflict.status = ConflictStatus.RESOLVED
        conflict.resolved_by_id = resolution.resolved_by.id
        conflict.resolved_at = datetime.now(UTC)

    await session.commit()
    return row


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
    sha256: str,
) -> DataImportBatch | None:
    existing = await session.scalar(
        select(DataImportBatch)
        .join(
            DataArtifact,
            and_(
                DataArtifact.org_id == DataImportBatch.org_id,
                DataArtifact.account_id == DataImportBatch.account_id,
                DataArtifact.batch_id == DataImportBatch.id,
            ),
        )
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
            DataImportBatch.committed_at.is_(None),
            DataImportBatch.revoked_at.is_(None),
            DataArtifact.sha256 == sha256,
        )
        .order_by(DataImportBatch.id.desc())
    )
    if existing is None:
        return None
    artifact = existing.artifacts[0] if existing.artifacts else None
    if artifact is None or not storage.exists(artifact.storage_key):
        return None
    return existing


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
