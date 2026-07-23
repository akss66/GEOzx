from __future__ import annotations

import json
from datetime import date

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import (
    Account,
    AccountMembership,
    AccountMetricSnapshot,
    AudienceProfileItem,
    AudienceProfileSnapshot,
    BenchmarkSnapshot,
    Client,
    ClientMembership,
    DataImportBatch,
    User,
)
from app.models.enums import DataSourceKind, Platform, UserRole, WorkspaceRole


async def _login(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def manual_entry_setup(session, admin):
    workspace = Client(org_id=admin.org_id, name="Manual data workspace")
    account = Account(
        org_id=admin.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="Manual data account",
    )
    operator = User(
        org_id=admin.org_id,
        email="manual-entry-operator@test.com",
        hashed_password=hash_password("operator-pw-123"),
        display_name="Manual operator",
        role=UserRole.USER,
        account_scope_mode="selected",
    )
    session.add_all([workspace, account, operator])
    await session.flush()
    session.add_all(
        [
            ClientMembership(client=workspace, user=operator, role=WorkspaceRole.OPERATOR),
            AccountMembership(user=operator, account=account),
        ]
    )
    await session.commit()
    return {"account": account, "operator": operator}


@pytest.fixture
async def manual_operator_token(client, manual_entry_setup) -> str:
    operator = manual_entry_setup["operator"]
    return await _login(client, operator.email, "operator-pw-123")


def _account_period_payload() -> dict:
    return {
        "data_domain": "account_period_totals",
        "stat_date": "2026-07-21",
        "period_start": "2026-07-15",
        "period_end": "2026-07-21",
        "account_metrics": {
            "follower_count": 1280,
            "follower_delta": -17,
            "total_play": 578,
            "engagement_rate": 0.0,
        },
    }


@pytest.mark.asyncio
async def test_structured_manual_preview_commits_with_manual_provenance(
    client,
    session,
    manual_entry_setup,
    manual_operator_token,
):
    account = manual_entry_setup["account"]
    preview = await client.post(
        f"/account-data/{account.id}/manual-previews",
        headers=_auth(manual_operator_token),
        data={"payload": json.dumps(_account_period_payload())},
    )

    assert preview.status_code == 201
    body = preview.json()
    assert body["source_kind"] == "manual_entry"
    assert body["template_code"] == "manual_account_period_v1"
    assert body["rows"][0]["status"] == "ready"
    assert body["rows"][0]["normalized_values"]["follower_count"] == 1280

    committed = await client.post(
        f"/account-data/{account.id}/imports/{body['id']}/commit",
        headers=_auth(manual_operator_token),
    )
    assert committed.status_code == 200

    snapshot = await session.scalar(
        select(AccountMetricSnapshot).where(
            AccountMetricSnapshot.import_batch_id == body["id"]
        )
    )
    assert snapshot is not None
    assert snapshot.source_kind is DataSourceKind.MANUAL_ENTRY
    assert snapshot.stat_date == date(2026, 7, 21)
    assert snapshot.follower_count == 1280
    assert snapshot.follower_delta == -17
    assert snapshot.total_play == 578
    assert snapshot.total_exposure is None


@pytest.mark.asyncio
async def test_screenshot_candidates_cannot_commit_before_confirmation(
    client,
    session,
    manual_entry_setup,
    manual_operator_token,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account = manual_entry_setup["account"]
    png = b"\x89PNG\r\n\x1a\n" + b"verified screenshot evidence"
    preview = await client.post(
        f"/account-data/{account.id}/manual-previews",
        headers=_auth(manual_operator_token),
        data={"payload": json.dumps(_account_period_payload())},
        files={"screenshot": ("diagnosis.png", png, "image/png")},
    )

    assert preview.status_code == 201
    body = preview.json()
    assert body["source_kind"] == "screenshot_verified"
    assert body["rows"][0]["status"] == "needs_resolution"
    assert body["artifacts"][0]["filename"] == "diagnosis.png"

    blocked = await client.post(
        f"/account-data/{account.id}/imports/{body['id']}/commit",
        headers=_auth(manual_operator_token),
    )
    assert blocked.status_code == 409

    confirmed = await client.patch(
        f"/account-data/{account.id}/imports/{body['id']}/rows/1",
        headers=_auth(manual_operator_token),
        json={"confirmed": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "ready"
    assert confirmed.json()["resolution_outcome"] == "confirmed"

    committed = await client.post(
        f"/account-data/{account.id}/imports/{body['id']}/commit",
        headers=_auth(manual_operator_token),
    )
    assert committed.status_code == 200

    batch = await session.get(DataImportBatch, body["id"])
    assert batch is not None
    assert batch.source_kind is DataSourceKind.SCREENSHOT_VERIFIED


@pytest.mark.asyncio
async def test_manual_preview_rejects_disguised_non_image_evidence(
    client,
    manual_entry_setup,
    manual_operator_token,
):
    account = manual_entry_setup["account"]
    response = await client.post(
        f"/account-data/{account.id}/manual-previews",
        headers=_auth(manual_operator_token),
        data={"payload": json.dumps(_account_period_payload())},
        files={"screenshot": ("diagnosis.png", b"not an image", "image/png")},
    )

    assert response.status_code == 422
    assert "image" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_manual_preview_rejects_screenshot_larger_than_five_megabytes(
    client,
    manual_entry_setup,
    manual_operator_token,
):
    account = manual_entry_setup["account"]
    oversized_png = b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024)
    response = await client.post(
        f"/account-data/{account.id}/manual-previews",
        headers=_auth(manual_operator_token),
        data={"payload": json.dumps(_account_period_payload())},
        files={"screenshot": ("diagnosis.png", oversized_png, "image/png")},
    )

    assert response.status_code == 422
    assert "5 mb" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_manual_audience_profile_commits_items_and_revoke_removes_projection(
    client,
    session,
    admin,
    manual_entry_setup,
    manual_operator_token,
):
    account = manual_entry_setup["account"]
    preview = await client.post(
        f"/account-data/{account.id}/manual-previews",
        headers=_auth(manual_operator_token),
        data={
            "payload": json.dumps(
                {
                    "data_domain": "audience_dimension",
                    "stat_date": "2026-07-21",
                    "dimension": "age",
                    "total_audience": 100,
                    "audience_items": [
                        {"label": "under_23", "value": "<23", "ratio": 0.68},
                        {"label": "age_24_30", "value": "24-30", "ratio": 0.04},
                    ],
                }
            )
        },
    )
    assert preview.status_code == 201
    body = preview.json()
    assert body["template_code"] == "manual_audience_dimension_v1"

    committed = await client.post(
        f"/account-data/{account.id}/imports/{body['id']}/commit",
        headers=_auth(manual_operator_token),
    )
    assert committed.status_code == 200

    snapshot = await session.scalar(
        select(AudienceProfileSnapshot).where(
            AudienceProfileSnapshot.import_batch_id == body["id"]
        )
    )
    assert snapshot is not None
    assert snapshot.source_kind is DataSourceKind.MANUAL_ENTRY
    assert snapshot.dimension == "age"
    assert snapshot.total_audience == 100
    items = list(
        (
            await session.scalars(
                select(AudienceProfileItem)
                .where(AudienceProfileItem.snapshot_id == snapshot.id)
                .order_by(AudienceProfileItem.rank)
            )
        ).all()
    )
    assert [(item.value, item.ratio) for item in items] == [("<23", 0.68), ("24-30", 0.04)]

    admin_token = await _login(client, admin.email, "admin-pw-123")
    revoked = await client.post(
        f"/account-data/{account.id}/imports/{body['id']}/revoke",
        headers=_auth(admin_token),
    )
    assert revoked.status_code == 200
    assert await session.get(AudienceProfileSnapshot, snapshot.id) is None
    remaining_items = await session.scalar(
        select(AudienceProfileItem.id).where(AudienceProfileItem.snapshot_id == snapshot.id)
    )
    assert remaining_items is None


@pytest.mark.asyncio
async def test_manual_benchmark_preserves_metric_values_and_sample_sizes(
    client,
    session,
    manual_entry_setup,
    manual_operator_token,
):
    account = manual_entry_setup["account"]
    preview = await client.post(
        f"/account-data/{account.id}/manual-previews",
        headers=_auth(manual_operator_token),
        data={
            "payload": json.dumps(
                {
                    "data_domain": "benchmark",
                    "stat_date": "2026-07-21",
                    "benchmark_code": "peer_weekly_diagnosis",
                    "benchmark_metrics": [
                        {"metric_code": "completion_rate", "metric_value": 0.18, "sample_size": 20},
                        {"metric_code": "avg_play", "metric_value": 125, "sample_size": 20},
                    ],
                }
            )
        },
    )
    assert preview.status_code == 201
    body = preview.json()
    assert body["template_code"] == "manual_benchmark_v1"

    committed = await client.post(
        f"/account-data/{account.id}/imports/{body['id']}/commit",
        headers=_auth(manual_operator_token),
    )
    assert committed.status_code == 200

    snapshots = list(
        (
            await session.scalars(
                select(BenchmarkSnapshot)
                .where(BenchmarkSnapshot.import_batch_id == body["id"])
                .order_by(BenchmarkSnapshot.metric_code)
            )
        ).all()
    )
    assert [(item.metric_code, item.metric_value, item.sample_size) for item in snapshots] == [
        ("avg_play", 125.0, 20),
        ("completion_rate", 0.18, 20),
    ]
    assert all(item.source_kind is DataSourceKind.MANUAL_ENTRY for item in snapshots)
