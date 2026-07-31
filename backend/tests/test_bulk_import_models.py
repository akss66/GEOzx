from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import (
    Account,
    DataFieldObservation,
    DataImportBatch,
    DataImportFile,
    DataImportJob,
    DataImportRow,
)
from app.models.enums import (
    DataSourceKind,
    ImportBatchStatus,
    ImportFileStatus,
    ImportJobStatus,
    ImportRowStatus,
    Platform,
)


@pytest.fixture
async def account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Bulk import account",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_one_import_job_owns_many_files_and_each_file_owns_many_datasets(
    session,
    admin,
    account,
):
    job = DataImportJob(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        client_request_id="bulk-request-001",
        status=ImportJobStatus.QUEUED,
        file_count=2,
    )
    session.add(job)
    await session.flush()
    first_file = DataImportFile(
        org_id=admin.org_id,
        account_id=account.id,
        job_id=job.id,
        ordinal=1,
        filename="account-and-works.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        byte_size=2048,
        sha256="a" * 64,
        storage_key="account-data/bulk/account-and-works.xlsx",
        status=ImportFileStatus.QUEUED,
    )
    second_file = DataImportFile(
        org_id=admin.org_id,
        account_id=account.id,
        job_id=job.id,
        ordinal=2,
        filename="audience.csv",
        content_type="text/csv",
        byte_size=512,
        sha256="b" * 64,
        storage_key="account-data/bulk/audience.csv",
        status=ImportFileStatus.QUEUED,
    )
    session.add_all([first_file, second_file])
    await session.flush()
    session.add_all(
        [
            DataImportBatch(
                org_id=admin.org_id,
                account_id=account.id,
                created_by_id=admin.id,
                job_id=job.id,
                job_file_id=first_file.id,
                source_kind=DataSourceKind.PLATFORM_EXPORT,
                status=ImportBatchStatus.UPLOADED,
                template_code="douyin_daily_play_v1",
                content_sha256=first_file.sha256,
                sheet_name="每日播放",
                dataset_ordinal=1,
            ),
            DataImportBatch(
                org_id=admin.org_id,
                account_id=account.id,
                created_by_id=admin.id,
                job_id=job.id,
                job_file_id=first_file.id,
                source_kind=DataSourceKind.PLATFORM_EXPORT,
                status=ImportBatchStatus.UPLOADED,
                template_code="douyin_work_list_v1",
                content_sha256=first_file.sha256,
                sheet_name="作品列表",
                dataset_ordinal=2,
            ),
        ]
    )
    await session.commit()
    await session.refresh(job, attribute_names=["files"])
    await session.refresh(first_file, attribute_names=["datasets"])

    assert [item.ordinal for item in job.files] == [1, 2]
    assert [item.template_code for item in first_file.datasets] == [
        "douyin_daily_play_v1",
        "douyin_work_list_v1",
    ]


@pytest.mark.asyncio
async def test_field_observation_preserves_zero_and_account_scoped_provenance(
    session,
    admin,
    account,
):
    batch = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_daily_play_v1",
        content_sha256="c" * 64,
        confirmed_sequence=19,
    )
    session.add(batch)
    await session.flush()
    row = DataImportRow(
        org_id=admin.org_id,
        account_id=account.id,
        batch_id=batch.id,
        row_number=2,
        status=ImportRowStatus.COMMITTED,
        normalized_values={"play": 0},
    )
    session.add(row)
    await session.flush()
    observation = DataFieldObservation(
        org_id=admin.org_id,
        account_id=account.id,
        import_batch_id=batch.id,
        import_row_id=row.id,
        domain="account_metrics",
        entity_key=f"account:{account.id}",
        stat_date=date(2026, 7, 31),
        field_name="play",
        value={"kind": "int", "value": 0},
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        source_priority=300,
        confirmed_sequence=19,
        active=True,
    )
    session.add(observation)
    await session.commit()

    assert observation.value == {"kind": "int", "value": 0}
    assert observation.account_id == batch.account_id == row.account_id
    assert observation.active is True


@pytest.mark.asyncio
async def test_database_rejects_cross_account_bulk_file_and_observation_links(
    session,
    admin,
    account,
):
    await session.execute(text("PRAGMA foreign_keys=ON"))
    other_account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Other bulk import account",
    )
    session.add(other_account)
    await session.flush()
    job = DataImportJob(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        client_request_id="bulk-request-scope",
        status=ImportJobStatus.QUEUED,
        file_count=1,
    )
    session.add(job)
    await session.commit()
    org_id = admin.org_id
    actor_id = admin.id
    account_id = account.id
    other_account_id = other_account.id
    job_id = job.id

    session.add(
        DataImportFile(
            org_id=org_id,
            account_id=other_account_id,
            job_id=job_id,
            ordinal=1,
            filename="cross-account.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            byte_size=256,
            sha256="d" * 64,
            storage_key="account-data/bulk/cross-account.xlsx",
            status=ImportFileStatus.QUEUED,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    batch = DataImportBatch(
        org_id=org_id,
        account_id=account_id,
        created_by_id=actor_id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_daily_play_v1",
        content_sha256="e" * 64,
        confirmed_sequence=20,
    )
    session.add(batch)
    await session.commit()
    session.add(
        DataFieldObservation(
            org_id=org_id,
            account_id=other_account_id,
            import_batch_id=batch.id,
            domain="account_metrics",
            entity_key=f"account:{other_account_id}",
            stat_date=date(2026, 7, 31),
            field_name="play",
            value={"kind": "int", "value": 1},
            source_kind=DataSourceKind.PLATFORM_EXPORT,
            source_priority=300,
            confirmed_sequence=20,
            active=True,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
