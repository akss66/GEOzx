from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core import storage
from app.models import (
    Account,
    AccountMetricSnapshot,
    DataFieldObservation,
    DataImportFile,
    DataImportJob,
    PlatformContentRecord,
)
from app.models.enums import (
    ContentIdentityConfidence,
    ImportFileStatus,
    ImportJobStatus,
    Platform,
)
from app.services.data_import.jobs import (
    JobUpload,
    create_import_job,
    enqueue_account_data_import_job,
    process_import_job,
    retry_import_file,
)
from app.worker import recover_account_data_import_jobs
from tests.test_data_import_templates import (
    DAILY_HEADERS,
    SINGLE_CONTENT_HEADERS,
    workbook_bytes,
)


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
async def test_enqueue_uses_file_count_as_dispatch_revision(monkeypatch):
    enqueued: list[tuple[int, str]] = []

    class FakePool:
        async def enqueue_job(self, _name, job_id, *, _job_id):
            enqueued.append((job_id, _job_id))

    async def fake_pool():
        return FakePool()

    monkeypatch.setattr("app.services.data_import.jobs.get_arq_pool", fake_pool)

    await enqueue_account_data_import_job(41, dispatch_revision=2)
    await enqueue_account_data_import_job(41, dispatch_revision=3)

    assert enqueued == [
        (41, "account-data-import-job:41:2"),
        (41, "account-data-import-job:41:3"),
    ]


@pytest.mark.asyncio
async def test_recovery_reenqueues_lost_and_stale_import_jobs(
    session,
    admin,
    account,
    monkeypatch,
):
    job = DataImportJob(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        client_request_id="lost-queue-message",
        status=ImportJobStatus.QUEUED,
        file_count=1,
    )
    stale = DataImportJob(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        client_request_id="stale-processing-job",
        status=ImportJobStatus.PROCESSING,
        file_count=1,
        updated_at=datetime.now(UTC) - timedelta(minutes=31),
    )
    fresh = DataImportJob(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        client_request_id="fresh-processing-job",
        status=ImportJobStatus.PROCESSING,
        file_count=1,
    )
    session.add_all([job, stale, fresh])
    await session.commit()
    await session.refresh(job)
    await session.refresh(stale)

    jobs: list[tuple[str, int, str]] = []

    class FakePool:
        async def enqueue_job(self, name, job_id, *, _job_id):
            jobs.append((name, job_id, _job_id))
            return object()

    monkeypatch.setattr("app.worker.async_session", lambda: session)

    recovered = await recover_account_data_import_jobs({"redis": FakePool()})

    assert recovered == 2
    assert [(item[0], item[1]) for item in jobs] == [
        ("execute_account_data_import_job", job.id),
        ("execute_account_data_import_job", stale.id),
    ]
    assert jobs[0][2].startswith(f"account-data-import-job:{job.id}:recovery:")
    assert jobs[1][2].startswith(
        f"account-data-import-job:{stale.id}:recovery:"
    )


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
async def test_retry_reuses_unresolved_preview_without_leaking_unique_constraint(
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    content = workbook_bytes(
        SINGLE_CONTENT_HEADERS[:3],
        [
            ["作品 A", "2026-07-30 12:00:00", 100],
            ["作品 B", "2026-07-30 13:00:00", 200],
        ],
    )
    session.add_all(
        [
            PlatformContentRecord(
                org_id=account.org_id,
                account_id=account.id,
                platform=Platform.DOUYIN,
                external_content_id=f"ambiguous-{index}",
                title="作品 A",
                published_at=datetime(2026, 7, 30, 12, 0),
                identity_confidence=ContentIdentityConfidence.CONFIRMED,
            )
            for index in range(2)
        ]
    )
    await session.commit()
    job, _ = await create_import_job(
        session,
        user=admin,
        account=account,
        client_request_id="worker-unresolved-retry",
        uploads=[
            JobUpload(
                filename="single-content.xlsx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                content=content,
            )
        ],
    )
    job = await process_import_job(session, job_id=job.id)
    original = job.files[0]
    assert original.status is ImportFileStatus.FAILED
    assert original.error_payload["failures"][0]["code"] == (
        "manual_resolution_required"
    )

    retried = await retry_import_file(
        session,
        org_id=account.org_id,
        account_id=account.id,
        job_id=job.id,
        file_id=original.id,
    )
    job = await process_import_job(session, job_id=job.id)
    await session.refresh(retried)

    assert retried.status is ImportFileStatus.FAILED
    failure = retried.error_payload["failures"][0]
    assert failure["code"] == "manual_resolution_required"
    assert "IntegrityError" not in failure["message"]
    assert "uq_data_import_batches_active_preview_identity" not in failure["message"]


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
