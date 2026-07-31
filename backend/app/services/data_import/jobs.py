from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import storage
from app.core.events import get_arq_pool, publish_realtime_event
from app.models import Account, DataImportFile, DataImportJob, User
from app.models.enums import ImportBatchStatus, ImportFileStatus, ImportJobStatus
from app.services.data_import.parser import (
    MAX_FILE_BYTES,
    SUPPORTED_EXTENSIONS,
    ParseFailure,
    parse_source_file,
)
from app.services.data_import.service import (
    DataImportCommitConflictError,
    commit_batch,
    create_job_dataset_preview,
)

MAX_IMPORT_JOB_FILES = 20
MAX_IMPORT_JOB_BYTES = 50 * 1024 * 1024


class DataImportJobNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class JobUpload:
    filename: str
    content_type: str
    content: bytes


async def create_import_job(
    session: AsyncSession,
    *,
    user: User,
    account: Account,
    client_request_id: str,
    uploads: list[JobUpload],
) -> tuple[DataImportJob, bool]:
    request_id = client_request_id.strip()
    if not request_id or len(request_id) > 120:
        raise ValueError("client_request_id must contain 1 to 120 characters")
    if not uploads:
        raise ValueError("at least one file is required")
    if len(uploads) > MAX_IMPORT_JOB_FILES:
        raise ValueError(f"no more than {MAX_IMPORT_JOB_FILES} files are allowed")
    total_bytes = sum(len(item.content) for item in uploads)
    if total_bytes > MAX_IMPORT_JOB_BYTES:
        raise ValueError("the combined upload is larger than 50 MB")
    for upload in uploads:
        _validate_upload(upload)

    existing = await session.scalar(
        select(DataImportJob)
        .options(
            selectinload(DataImportJob.files).selectinload(DataImportFile.datasets)
        )
        .where(
            DataImportJob.org_id == account.org_id,
            DataImportJob.account_id == account.id,
            DataImportJob.client_request_id == request_id,
        )
    )
    if existing is not None:
        return existing, False

    savepoint = await session.begin_nested()
    try:
        job = DataImportJob(
            org_id=account.org_id,
            account_id=account.id,
            created_by_id=user.id,
            client_request_id=request_id,
            status=ImportJobStatus.QUEUED,
            file_count=len(uploads),
        )
        session.add(job)
        await session.flush()
    except IntegrityError:
        await savepoint.rollback()
        winner = await session.scalar(
            select(DataImportJob)
            .options(
                selectinload(DataImportJob.files).selectinload(
                    DataImportFile.datasets
                )
            )
            .where(
                DataImportJob.org_id == account.org_id,
                DataImportJob.account_id == account.id,
                DataImportJob.client_request_id == request_id,
            )
        )
        if winner is None:
            raise
        return winner, False
    await savepoint.commit()
    written_keys: list[str] = []
    try:
        for ordinal, upload in enumerate(uploads, start=1):
            digest = hashlib.sha256(upload.content).hexdigest()
            extension = Path(upload.filename).suffix.lower()
            storage_key = (
                f"orgs/{account.org_id}/accounts/{account.id}/import-jobs/"
                f"{job.id}/{ordinal}-{digest[:16]}-{uuid4().hex}{extension}"
            )
            storage.save_bytes(storage_key, upload.content)
            written_keys.append(storage_key)
            session.add(
                DataImportFile(
                    org_id=account.org_id,
                    account_id=account.id,
                    job_id=job.id,
                    ordinal=ordinal,
                    filename=_safe_filename(upload.filename, extension),
                    content_type=upload.content_type or "application/octet-stream",
                    byte_size=len(upload.content),
                    sha256=digest,
                    storage_key=storage_key,
                    status=ImportFileStatus.QUEUED,
                    error_payload={},
                )
            )
        await session.commit()
    except Exception:
        await session.rollback()
        for storage_key in written_keys:
            storage.resolve(storage_key).unlink(missing_ok=True)
        raise
    return await load_import_job(
        session,
        org_id=account.org_id,
        account_id=account.id,
        job_id=job.id,
    ), True


async def load_import_job(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    job_id: int,
) -> DataImportJob:
    job = await session.scalar(
        select(DataImportJob)
        .options(
            selectinload(DataImportJob.files).selectinload(DataImportFile.datasets)
        )
        .where(
            DataImportJob.id == job_id,
            DataImportJob.org_id == org_id,
            DataImportJob.account_id == account_id,
        )
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise DataImportJobNotFoundError("import job does not exist")
    return job


async def enqueue_account_data_import_job(job_id: int) -> None:
    pool = await get_arq_pool()
    await pool.enqueue_job(
        "execute_account_data_import_job",
        job_id,
        _job_id=f"account-data-import-job:{job_id}",
    )


async def process_import_job(
    session: AsyncSession,
    *,
    job_id: int,
) -> DataImportJob:
    job = await session.scalar(
        select(DataImportJob)
        .options(
            selectinload(DataImportJob.files).selectinload(DataImportFile.datasets)
        )
        .where(DataImportJob.id == job_id)
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise DataImportJobNotFoundError("import job does not exist")
    account = await session.get(Account, job.account_id)
    user = await session.get(User, job.created_by_id)
    if (
        account is None
        or account.org_id != job.org_id
        or user is None
        or user.org_id != job.org_id
    ):
        job.status = ImportJobStatus.FAILED
        job.completed_at = datetime.now(UTC)
        await session.commit()
        return await load_import_job(
            session,
            org_id=job.org_id,
            account_id=job.account_id,
            job_id=job.id,
        )

    job.status = ImportJobStatus.PROCESSING
    job.started_at = job.started_at or datetime.now(UTC)
    job.completed_at = None
    await session.commit()
    for job_file in sorted(job.files, key=lambda item: item.ordinal):
        if job_file.status not in {
            ImportFileStatus.QUEUED,
            ImportFileStatus.PROCESSING,
        }:
            continue
        duplicate = await session.scalar(
            select(DataImportFile).where(
                DataImportFile.org_id == job.org_id,
                DataImportFile.account_id == job.account_id,
                DataImportFile.sha256 == job_file.sha256,
                DataImportFile.id != job_file.id,
                DataImportFile.status == ImportFileStatus.COMPLETED,
                DataImportFile.created_at <= job_file.created_at,
            )
        )
        if duplicate is not None:
            job_file.status = ImportFileStatus.COMPLETED
            job_file.completed_at = datetime.now(UTC)
            job_file.error_payload = {"duplicate_of_file_id": duplicate.id}
            await _checkpoint_job(session, job)
            continue
        await _process_import_file(
            session,
            job=job,
            job_file=job_file,
            account=account,
            user=user,
        )
        await _checkpoint_job(session, job)

    await _checkpoint_job(session, job, terminal=True)
    return await load_import_job(
        session,
        org_id=job.org_id,
        account_id=job.account_id,
        job_id=job.id,
    )


async def retry_import_file(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    job_id: int,
    file_id: int,
) -> DataImportFile:
    job = await load_import_job(
        session,
        org_id=org_id,
        account_id=account_id,
        job_id=job_id,
    )
    source = next((item for item in job.files if item.id == file_id), None)
    if source is None:
        raise DataImportJobNotFoundError("import file does not exist")
    if source.status not in {
        ImportFileStatus.FAILED,
        ImportFileStatus.PARTIALLY_COMPLETED,
    }:
        raise ValueError("only failed or partially completed files can be retried")
    ordinal = int(
        await session.scalar(
            select(func.max(DataImportFile.ordinal)).where(
                DataImportFile.job_id == job.id
            )
        )
        or 0
    ) + 1
    retried = DataImportFile(
        org_id=job.org_id,
        account_id=job.account_id,
        job_id=job.id,
        retry_of_file_id=source.id,
        ordinal=ordinal,
        filename=source.filename,
        content_type=source.content_type,
        byte_size=source.byte_size,
        sha256=source.sha256,
        storage_key=source.storage_key,
        status=ImportFileStatus.QUEUED,
        error_payload={},
    )
    session.add(retried)
    job.file_count += 1
    job.status = ImportJobStatus.QUEUED
    job.completed_at = None
    await session.commit()
    await session.refresh(retried)
    return retried


async def _process_import_file(
    session: AsyncSession,
    *,
    job: DataImportJob,
    job_file: DataImportFile,
    account: Account,
    user: User,
) -> None:
    job_file.status = ImportFileStatus.PROCESSING
    job_file.started_at = job_file.started_at or datetime.now(UTC)
    job_file.completed_at = None
    job_file.error_payload = {}
    await session.commit()
    successes = 0
    failures: list[dict[str, object]] = []
    try:
        parsed = parse_source_file(
            job_file.filename,
            storage.resolve(job_file.storage_key).read_bytes(),
        )
    except (OSError, ParseFailure, ValueError) as exc:
        job_file.status = ImportFileStatus.FAILED
        job_file.completed_at = datetime.now(UTC)
        job_file.error_payload = {
            "code": getattr(exc, "code", "file_processing_failed"),
            "message": str(exc),
        }
        await session.commit()
        return

    failures.extend(
        {
            "dataset_ordinal": item.dataset_ordinal,
            "sheet_name": item.sheet_name,
            "code": item.code,
            "message": item.message,
        }
        for item in parsed.failures
    )
    for dataset in parsed.datasets:
        try:
            batch = await create_job_dataset_preview(
                session,
                user=user,
                account=account,
                job_file=job_file,
                dataset=dataset,
            )
            batch = await commit_batch(
                session,
                org_id=job.org_id,
                account_id=job.account_id,
                batch_id=batch.id,
                actor=user,
            )
            if batch.status is ImportBatchStatus.COMMITTED:
                successes += 1
        except DataImportCommitConflictError as exc:
            await session.commit()
            failures.append(
                {
                    "dataset_ordinal": dataset.dataset_ordinal,
                    "sheet_name": dataset.sheet_name,
                    "code": "manual_resolution_required",
                    "message": str(exc),
                }
            )
        except Exception as exc:  # noqa: BLE001 - isolate one dataset/file
            await session.rollback()
            failures.append(
                {
                    "dataset_ordinal": dataset.dataset_ordinal,
                    "sheet_name": dataset.sheet_name,
                    "code": getattr(exc, "code", "dataset_processing_failed"),
                    "message": str(exc),
                }
            )

    if successes and failures:
        job_file.status = ImportFileStatus.PARTIALLY_COMPLETED
    elif successes:
        job_file.status = ImportFileStatus.COMPLETED
    else:
        job_file.status = ImportFileStatus.FAILED
    job_file.completed_at = datetime.now(UTC)
    job_file.error_payload = {"failures": failures} if failures else {}
    await session.commit()


async def _checkpoint_job(
    session: AsyncSession,
    job: DataImportJob,
    *,
    terminal: bool = False,
) -> None:
    await session.refresh(job, attribute_names=["files"])
    job.completed_file_count = sum(
        item.status is ImportFileStatus.COMPLETED for item in job.files
    )
    job.failed_file_count = sum(
        item.status in {
            ImportFileStatus.FAILED,
            ImportFileStatus.PARTIALLY_COMPLETED,
        }
        for item in job.files
    )
    if terminal:
        if job.failed_file_count == 0:
            job.status = ImportJobStatus.COMPLETED
        elif job.completed_file_count == 0:
            job.status = ImportJobStatus.FAILED
        else:
            job.status = ImportJobStatus.COMPLETED_WITH_ERRORS
        job.completed_at = datetime.now(UTC)
    await session.commit()
    await publish_realtime_event(
        "account_data.import_job.progress",
        {
            "org_id": job.org_id,
            "account_id": job.account_id,
            "job_id": job.id,
            "status": job.status.value,
            "file_count": job.file_count,
            "completed_file_count": job.completed_file_count,
            "failed_file_count": job.failed_file_count,
        },
    )


def _validate_upload(upload: JobUpload) -> None:
    extension = Path(upload.filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"{upload.filename}: unsupported file extension")
    if not upload.content:
        raise ValueError(f"{upload.filename}: empty files are not supported")
    if len(upload.content) > MAX_FILE_BYTES:
        raise ValueError(f"{upload.filename}: file is larger than 10 MB")


def _safe_filename(filename: str, extension: str) -> str:
    stem = Path(filename).stem.strip() or "upload"
    safe = "".join(
        character if character.isalnum() or character in "._- " else "_"
        for character in stem
    )
    return f"{safe[:220]}{extension}"
