from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import (
    Account,
    AccountMembership,
    Client,
    ClientMembership,
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
    DataSourceKind,
    ImportBatchStatus,
    ImportRowStatus,
    MetricSource,
    Platform,
    UserRole,
    WorkspaceRole,
)
from tests.test_data_import_templates import WORK_LIST_HEADERS, workbook_bytes


async def _login(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _workbook_payload(*, title: str = "作品 A") -> bytes:
    return workbook_bytes(
        WORK_LIST_HEADERS,
        [[
            title,
            "2026-07-18 14:11:20",
            "1min-视频",
            "公开",
            "81",
            "0.087500",
            "0.375000",
            "-",
            "0.375000",
            "9.53",
            "6",
            "0",
            "3",
            "0",
            "3",
            "0",
        ]],
    )


@pytest.fixture
async def account_access_setup(session, admin):
    workspace = Client(org_id=admin.org_id, name="Account data workspace")
    account = Account(
        org_id=admin.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="Import account",
    )
    other_account = Account(
        org_id=admin.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="Other account",
    )
    operator = User(
        org_id=admin.org_id,
        email="operator-account-data@test.com",
        hashed_password=hash_password("operator-pw-123"),
        display_name="Operator",
        role=UserRole.USER,
        account_scope_mode="selected",
    )
    reviewer = User(
        org_id=admin.org_id,
        email="reviewer-account-data@test.com",
        hashed_password=hash_password("reviewer-pw-123"),
        display_name="Reviewer",
        role=UserRole.USER,
        account_scope_mode="selected",
    )
    lead = User(
        org_id=admin.org_id,
        email="lead-account-data@test.com",
        hashed_password=hash_password("lead-pw-123"),
        display_name="Lead",
        role=UserRole.USER,
        account_scope_mode="selected",
    )
    outsider = User(
        org_id=admin.org_id,
        email="outsider-account-data@test.com",
        hashed_password=hash_password("outsider-pw-123"),
        display_name="Outsider",
        role=UserRole.USER,
        account_scope_mode="selected",
    )
    session.add_all([workspace, account, other_account, operator, reviewer, lead, outsider])
    await session.flush()
    session.add_all(
        [
            ClientMembership(client=workspace, user=operator, role=WorkspaceRole.OPERATOR),
            ClientMembership(client=workspace, user=reviewer, role=WorkspaceRole.REVIEWER),
            ClientMembership(client=workspace, user=lead, role=WorkspaceRole.LEAD),
            AccountMembership(user=operator, account=account),
            AccountMembership(user=reviewer, account=account),
            AccountMembership(user=lead, account=account),
        ]
    )
    await session.commit()
    return {
        "account": account,
        "other_account": other_account,
        "operator": operator,
        "reviewer": reviewer,
        "lead": lead,
        "outsider": outsider,
    }


@pytest.fixture
async def operator_token(client, account_access_setup) -> str:
    operator = account_access_setup["operator"]
    return await _login(client, operator.email, "operator-pw-123")


@pytest.fixture
async def lead_token(client, account_access_setup) -> str:
    lead = account_access_setup["lead"]
    return await _login(client, lead.email, "lead-pw-123")


@pytest.mark.asyncio
async def test_operator_can_preview_resolve_commit_download_and_list_imports(
    client,
    session,
    admin,
    account_access_setup,
    operator_token,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account = account_access_setup["account"]
    candidate = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
        weak_fingerprint="作品 a|2026-07-18t14:11:20",
    )
    session.add(candidate)
    await session.commit()

    preview = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(operator_token),
        files={
            "file": (
                "works.xlsx",
                _workbook_payload(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert preview.status_code == 201
    preview_body = preview.json()
    batch_id = preview_body["id"]
    assert preview_body["status"] == "preview_ready"
    assert preview_body["row_count"] == 1
    assert preview_body["rows"][0]["status"] == "needs_resolution"
    assert preview_body["artifacts"][0]["filename"] == "works.xlsx"
    assert "storage_key" not in json.dumps(preview_body)
    assert str(tmp_path) not in json.dumps(preview_body)

    fetched = await client.get(
        f"/account-data/{account.id}/imports/{batch_id}",
        headers=_auth(operator_token),
    )
    assert fetched.status_code == 200
    assert fetched.json()["id"] == batch_id

    resolved = await client.patch(
        f"/account-data/{account.id}/imports/{batch_id}/rows/2",
        headers=_auth(operator_token),
        json={"selected_content_id": candidate.id},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "ready"
    assert resolved.json()["platform_content_record_id"] == candidate.id

    committed = await client.post(
        f"/account-data/{account.id}/imports/{batch_id}/commit",
        headers=_auth(operator_token),
    )
    assert committed.status_code == 200
    assert committed.json()["status"] == "committed"

    download = await client.get(
        preview_body["artifacts"][0]["download_url"],
        headers=_auth(operator_token),
    )
    assert download.status_code == 200
    assert download.content == _workbook_payload()

    history = await client.get(
        f"/account-data/{account.id}/imports",
        headers=_auth(operator_token),
    )
    assert history.status_code == 200
    assert [row["id"] for row in history.json()["items"]] == [batch_id]

    coverage = await client.get(
        f"/account-data/{account.id}/status",
        headers=_auth(operator_token),
    )
    assert coverage.status_code == 200
    assert coverage.json()["coverage"]["content_metrics"] == "available"
    assert coverage.json()["latest_confirmed_at"] is not None

    batch = await session.get(DataImportBatch, batch_id)
    assert batch is not None
    assert batch.status is ImportBatchStatus.COMMITTED
    assert batch.committed_at is not None

    row = await session.scalar(
        select(DataImportRow).where(
            DataImportRow.batch_id == batch_id,
            DataImportRow.row_number == 2,
        )
    )
    assert row is not None
    assert row.status is ImportRowStatus.COMMITTED
    assert row.projected_target_ids

    metric = await session.scalar(
        select(MetricSnapshot).where(MetricSnapshot.import_batch_id == batch_id)
    )
    assert metric is not None
    assert metric.account_id == account.id
    assert metric.platform_content_record_id == candidate.id
    assert metric.source is MetricSource.DOUYIN


@pytest.mark.asyncio
async def test_commit_rejects_unresolved_rows_without_partial_projection(
    client,
    session,
    admin,
    account_access_setup,
    operator_token,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account = account_access_setup["account"]
    candidate = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(candidate)
    await session.commit()

    preview = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(operator_token),
        files={
            "file": (
                "works.xlsx",
                _workbook_payload(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    batch_id = preview.json()["id"]

    committed = await client.post(
        f"/account-data/{account.id}/imports/{batch_id}/commit",
        headers=_auth(operator_token),
    )

    assert committed.status_code == 409
    assert "unresolved" in committed.json()["detail"]
    metric = await session.scalar(
        select(MetricSnapshot).where(MetricSnapshot.import_batch_id == batch_id)
    )
    assert metric is None
    batch = await session.get(DataImportBatch, batch_id)
    assert batch is not None
    assert batch.status is ImportBatchStatus.PREVIEW_READY


@pytest.mark.asyncio
async def test_lead_can_revoke_committed_batch_and_delete_owned_projections(
    client,
    session,
    account_access_setup,
    operator_token,
    lead_token,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account = account_access_setup["account"]

    preview = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(operator_token),
        files={
            "file": (
                "works.xlsx",
                _workbook_payload(title="Unique revoke title"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    batch_id = preview.json()["id"]
    committed = await client.post(
        f"/account-data/{account.id}/imports/{batch_id}/commit",
        headers=_auth(operator_token),
    )
    assert committed.status_code == 200

    revoked = await client.post(
        f"/account-data/{account.id}/imports/{batch_id}/revoke",
        headers=_auth(lead_token),
    )

    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    batch = await session.get(DataImportBatch, batch_id)
    assert batch is not None
    assert batch.status is ImportBatchStatus.REVOKED
    assert batch.revoked_at is not None
    metric = await session.scalar(
        select(MetricSnapshot).where(MetricSnapshot.import_batch_id == batch_id)
    )
    assert metric is None
    row = await session.scalar(
        select(DataImportRow).where(
            DataImportRow.batch_id == batch_id,
            DataImportRow.row_number == 2,
        )
    )
    assert row is not None
    assert row.status is ImportRowStatus.REVOKED


@pytest.mark.asyncio
async def test_revoke_creates_conflict_when_projection_was_superseded(
    client,
    session,
    account_access_setup,
    operator_token,
    lead_token,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account = account_access_setup["account"]

    preview = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(operator_token),
        files={
            "file": (
                "works.xlsx",
                _workbook_payload(title="Superseded title"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    batch_id = preview.json()["id"]
    committed = await client.post(
        f"/account-data/{account.id}/imports/{batch_id}/commit",
        headers=_auth(operator_token),
    )
    assert committed.status_code == 200

    row = await session.scalar(
        select(DataImportRow).where(
            DataImportRow.batch_id == batch_id,
            DataImportRow.row_number == 2,
        )
    )
    assert row is not None
    content_target = next(
        item for item in row.projected_target_ids if item["kind"] == "platform_content_record"
    )
    later_batch = DataImportBatch(
        org_id=account.org_id,
        account_id=account.id,
        created_by_id=account_access_setup["lead"].id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_work_list_v1",
        content_sha256="9" * 64,
        committed_at=datetime(2026, 7, 22, 9, 0, tzinfo=UTC),
    )
    session.add(later_batch)
    await session.flush()
    content = await session.get(PlatformContentRecord, content_target["id"])
    assert content is not None
    content.canonical_import_batch_id = later_batch.id
    await session.commit()

    revoked = await client.post(
        f"/account-data/{account.id}/imports/{batch_id}/revoke",
        headers=_auth(lead_token),
    )

    assert revoked.status_code == 409
    assert "superseded" in revoked.json()["detail"]
    conflict = await session.scalar(
        select(DataConflict).where(
            DataConflict.batch_id == batch_id,
            DataConflict.row_number == 2,
            DataConflict.conflict_code == "superseded_projection",
        )
    )
    assert conflict is not None
    assert conflict.status is ConflictStatus.OPEN
