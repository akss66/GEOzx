from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core import storage
from app.models import (
    Account,
    AccountMetricSnapshot,
    DataFieldObservation,
    DataImportFile,
)
from app.models.enums import ImportFileStatus, ImportJobStatus, Platform
from app.services.data_import.jobs import (
    JobUpload,
    create_import_job,
    process_import_job,
    retry_import_file,
)
from tests.test_data_import_templates import DAILY_HEADERS, workbook_bytes


@pytest.fixture
async def account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Bulk import worker account",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_worker_commits_four_files_when_fifth_file_fails(
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    uploads = [
        JobUpload(
            filename=f"daily-{day}.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=workbook_bytes(DAILY_HEADERS, [[f"2026-07-{day:02d}", day * 10]]),
        )
        for day in range(1, 5)
    ]
    uploads.append(
        JobUpload(
            filename="broken.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=b"not-an-xlsx",
        )
    )
    job, _ = await create_import_job(
        session,
        user=admin,
        account=account,
        client_request_id="worker-mixed",
        uploads=uploads,
    )

    job = await process_import_job(session, job_id=job.id)

    assert job.status is ImportJobStatus.COMPLETED_WITH_ERRORS
    assert job.completed_file_count == 4
    assert job.failed_file_count == 1
    assert [item.status for item in job.files] == [
        ImportFileStatus.COMPLETED,
        ImportFileStatus.COMPLETED,
        ImportFileStatus.COMPLETED,
        ImportFileStatus.COMPLETED,
        ImportFileStatus.FAILED,
    ]
    assert (
        await session.scalar(
            select(func.count(AccountMetricSnapshot.id)).where(
                AccountMetricSnapshot.account_id == account.id
            )
        )
        == 4
    )


@pytest.mark.asyncio
async def test_retry_processes_only_failed_file_and_does_not_duplicate_observations(
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    valid = JobUpload(
        filename="valid.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content=workbook_bytes(DAILY_HEADERS, [["2026-07-30", 30]]),
    )
    broken = JobUpload(
        filename="broken.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        content=b"not-an-xlsx",
    )
    job, _ = await create_import_job(
        session,
        user=admin,
        account=account,
        client_request_id="worker-retry",
        uploads=[valid, broken],
    )
    job = await process_import_job(session, job_id=job.id)
    failed = next(item for item in job.files if item.status is ImportFileStatus.FAILED)
    observation_count = await session.scalar(
        select(func.count(DataFieldObservation.id)).where(
            DataFieldObservation.account_id == account.id
        )
    )
    storage.resolve(failed.storage_key).write_bytes(
        workbook_bytes(DAILY_HEADERS, [["2026-07-31", 40]])
    )

    retried = await retry_import_file(
        session,
        org_id=account.org_id,
        account_id=account.id,
        job_id=job.id,
        file_id=failed.id,
    )
    job = await process_import_job(session, job_id=job.id)

    assert retried.retry_of_file_id == failed.id
    assert retried.status is ImportFileStatus.COMPLETED
    assert await session.scalar(
        select(func.count(DataFieldObservation.id)).where(
            DataFieldObservation.account_id == account.id
        )
    ) == observation_count + 1
    original_valid = await session.scalar(
        select(DataImportFile).where(
            DataImportFile.job_id == job.id,
            DataImportFile.retry_of_file_id.is_(None),
            DataImportFile.status == ImportFileStatus.COMPLETED,
        )
    )
    assert original_valid is not None
    assert len(original_valid.datasets) == 1


@pytest.mark.asyncio
async def test_duplicate_artifact_hash_does_not_write_duplicate_observations(
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    content = workbook_bytes(DAILY_HEADERS, [["2026-07-31", 50]])
    job, _ = await create_import_job(
        session,
        user=admin,
        account=account,
        client_request_id="worker-duplicate-hash",
        uploads=[
            JobUpload(
                filename="duplicate-a.xlsx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                content=content,
            ),
            JobUpload(
                filename="duplicate-b.xlsx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                content=content,
            ),
        ],
    )

    job = await process_import_job(session, job_id=job.id)

    assert job.status is ImportJobStatus.COMPLETED
    assert job.files[1].error_payload == {
        "duplicate_of_file_id": job.files[0].id
    }
    assert await session.scalar(
        select(func.count(DataFieldObservation.id)).where(
            DataFieldObservation.account_id == account.id
        )
    ) == 1
