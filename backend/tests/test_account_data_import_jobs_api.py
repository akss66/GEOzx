from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import Account, DataImportFile, DataImportJob
from app.models.enums import ImportFileStatus, Platform
from tests.test_data_import_templates import DAILY_HEADERS, workbook_bytes


async def _token(client) -> str:
    response = await client.post(
        "/auth/login",
        json={"email": "admin@test.com", "password": "admin-pw-123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Bulk import API account",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_create_import_job_accepts_repeated_files_and_persists_before_enqueue(
    client,
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    token = await _token(client)
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    enqueued: list[int] = []

    async def fake_enqueue(job_id: int) -> None:
        persisted = await session.get(DataImportJob, job_id)
        assert persisted is not None
        assert len(persisted.files) == 2
        enqueued.append(job_id)

    monkeypatch.setattr(
        "app.api.account_data.enqueue_account_data_import_job",
        fake_enqueue,
    )
    response = await client.post(
        f"/account-data/{account.id}/import-jobs",
        headers=_auth(token),
        data={"client_request_id": "bulk-request-1"},
        files=[
            (
                "files",
                (
                    "daily-a.xlsx",
                    workbook_bytes(DAILY_HEADERS, [["2026-07-30", 10]]),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
            (
                "files",
                (
                    "daily-b.xlsx",
                    workbook_bytes(DAILY_HEADERS, [["2026-07-31", 20]]),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
        ],
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["client_request_id"] == "bulk-request-1"
    assert payload["status"] == "queued"
    assert [item["filename"] for item in payload["files"]] == [
        "daily-a.xlsx",
        "daily-b.xlsx",
    ]
    assert enqueued == [payload["id"]]
    assert await session.scalar(select(func.count(DataImportFile.id))) == 2


@pytest.mark.asyncio
async def test_same_client_request_returns_original_job_without_duplicate_files(
    client,
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    token = await _token(client)
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    enqueued: list[int] = []

    async def fake_enqueue(job_id: int) -> None:
        enqueued.append(job_id)

    monkeypatch.setattr(
        "app.api.account_data.enqueue_account_data_import_job",
        fake_enqueue,
    )
    request = {
        "headers": _auth(token),
        "data": {"client_request_id": "bulk-idempotent"},
        "files": [
            (
                "files",
                (
                    "daily.xlsx",
                    workbook_bytes(DAILY_HEADERS, [["2026-07-31", 10]]),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            )
        ],
    }

    first = await client.post(
        f"/account-data/{account.id}/import-jobs",
        **request,
    )
    second = await client.post(
        f"/account-data/{account.id}/import-jobs",
        **request,
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert await session.scalar(select(func.count(DataImportJob.id))) == 1
    assert await session.scalar(select(func.count(DataImportFile.id))) == 1
    assert enqueued == [first.json()["id"], first.json()["id"]]


@pytest.mark.asyncio
async def test_get_import_job_is_account_scoped(
    client,
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    token = await _token(client)
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))

    async def fake_enqueue(job_id: int) -> None:
        return None

    monkeypatch.setattr(
        "app.api.account_data.enqueue_account_data_import_job",
        fake_enqueue,
    )
    created = await client.post(
        f"/account-data/{account.id}/import-jobs",
        headers=_auth(token),
        data={"client_request_id": "bulk-scoped"},
        files=[
            (
                "files",
                (
                    "daily.xlsx",
                    workbook_bytes(DAILY_HEADERS, [["2026-07-31", 10]]),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            )
        ],
    )
    other = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Other bulk import account",
    )
    session.add(other)
    await session.commit()

    response = await client.get(
        f"/account-data/{other.id}/import-jobs/{created.json()['id']}",
        headers=_auth(token),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_retry_endpoint_reuploads_only_a_failed_file(
    client,
    session,
    admin,
    account,
    monkeypatch,
    tmp_path,
):
    token = await _token(client)
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    enqueued: list[int] = []

    async def fake_enqueue(job_id: int) -> None:
        enqueued.append(job_id)

    monkeypatch.setattr(
        "app.api.account_data.enqueue_account_data_import_job",
        fake_enqueue,
    )
    created = await client.post(
        f"/account-data/{account.id}/import-jobs",
        headers=_auth(token),
        data={"client_request_id": "bulk-retry-api"},
        files=[
            (
                "files",
                (
                    "daily.xlsx",
                    workbook_bytes(DAILY_HEADERS, [["2026-07-31", 10]]),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
            (
                "files",
                (
                    "broken.xlsx",
                    b"not-an-xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ),
        ],
    )
    job_id = created.json()["id"]
    files = list(
        await session.scalars(
            select(DataImportFile)
            .where(DataImportFile.job_id == job_id)
            .order_by(DataImportFile.ordinal)
        )
    )
    files[0].status = ImportFileStatus.COMPLETED
    files[1].status = ImportFileStatus.FAILED
    await session.commit()

    replacement = workbook_bytes(DAILY_HEADERS, [["2026-07-31", 25]])
    response = await client.post(
        f"/account-data/{account.id}/import-jobs/{job_id}/files/{files[1].id}/retry",
        headers=_auth(token),
        files={
            "file": (
                "corrected.xlsx",
                replacement,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert len(payload["files"]) == 3
    assert payload["files"][-1]["retry_of_file_id"] == files[1].id
    assert payload["files"][-1]["filename"] == "corrected.xlsx"
    assert payload["files"][-1]["sha256"] != files[1].sha256
    assert payload["files"][-1]["status"] == "queued"
    assert enqueued == [job_id, job_id]


@pytest.mark.asyncio
async def test_create_import_job_rejects_more_than_twenty_files(
    client,
    admin,
    account,
):
    token = await _token(client)
    payload = workbook_bytes(DAILY_HEADERS, [["2026-07-31", 10]])
    response = await client.post(
        f"/account-data/{account.id}/import-jobs",
        headers=_auth(token),
        data={"client_request_id": "bulk-too-many"},
        files=[
            (
                "files",
                (
                    f"daily-{index}.xlsx",
                    payload,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            )
            for index in range(21)
        ],
    )

    assert response.status_code == 400
