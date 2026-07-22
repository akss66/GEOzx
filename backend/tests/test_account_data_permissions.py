from __future__ import annotations

import pytest

from tests import test_account_data_import_api as account_data_api

_auth = account_data_api._auth
_login = account_data_api._login
_workbook_payload = account_data_api._workbook_payload
account_access_setup = account_data_api.account_access_setup
operator_token = account_data_api.operator_token
lead_token = account_data_api.lead_token


@pytest.fixture
async def reviewer_token(client, account_access_setup) -> str:
    reviewer = account_access_setup["reviewer"]
    return await _login(client, reviewer.email, "reviewer-pw-123")


@pytest.fixture
async def outsider_token(client, account_access_setup) -> str:
    outsider = account_access_setup["outsider"]
    return await _login(client, outsider.email, "outsider-pw-123")


@pytest.fixture
async def admin_token(client, admin) -> str:
    return await _login(client, admin.email, "admin-pw-123")


@pytest.mark.asyncio
async def test_reviewer_upload_is_forbidden_and_unassigned_upload_is_not_found(
    client,
    account_access_setup,
    reviewer_token,
    outsider_token,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account = account_access_setup["account"]

    reviewer_response = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(reviewer_token),
        files={
            "file": (
                "works.xlsx",
                _workbook_payload(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    outsider_response = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(outsider_token),
        files={
            "file": (
                "works.xlsx",
                _workbook_payload(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert reviewer_response.status_code == 403
    assert outsider_response.status_code == 404


@pytest.mark.asyncio
async def test_cross_account_batch_access_and_artifact_download_are_account_scoped(
    client,
    account_access_setup,
    operator_token,
    outsider_token,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account = account_access_setup["account"]
    other_account = account_access_setup["other_account"]

    preview = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(operator_token),
        files={
            "file": (
                "works.xlsx",
                _workbook_payload(title="Cross account title"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert preview.status_code == 201
    batch_id = preview.json()["id"]
    download_url = preview.json()["artifacts"][0]["download_url"]

    cross_account = await client.get(
        f"/account-data/{other_account.id}/imports/{batch_id}",
        headers=_auth(operator_token),
    )
    outsider_download = await client.get(download_url, headers=_auth(outsider_token))

    assert cross_account.status_code == 404
    assert outsider_download.status_code == 404


@pytest.mark.asyncio
async def test_only_lead_or_admin_can_revoke_committed_batches(
    client,
    account_access_setup,
    operator_token,
    reviewer_token,
    lead_token,
    admin_token,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    account = account_access_setup["account"]

    first_preview = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(operator_token),
        files={
            "file": (
                "works.xlsx",
                _workbook_payload(title="Lead revoke title"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    first_batch_id = first_preview.json()["id"]
    first_commit = await client.post(
        f"/account-data/{account.id}/imports/{first_batch_id}/commit",
        headers=_auth(operator_token),
    )
    assert first_commit.status_code == 200

    operator_revoke = await client.post(
        f"/account-data/{account.id}/imports/{first_batch_id}/revoke",
        headers=_auth(operator_token),
    )
    reviewer_revoke = await client.post(
        f"/account-data/{account.id}/imports/{first_batch_id}/revoke",
        headers=_auth(reviewer_token),
    )
    lead_revoke = await client.post(
        f"/account-data/{account.id}/imports/{first_batch_id}/revoke",
        headers=_auth(lead_token),
    )

    assert operator_revoke.status_code == 403
    assert reviewer_revoke.status_code == 403
    assert lead_revoke.status_code == 200

    second_preview = await client.post(
        f"/account-data/{account.id}/imports",
        headers=_auth(operator_token),
        files={
            "file": (
                "works.xlsx",
                _workbook_payload(title="Admin revoke title"),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    second_batch_id = second_preview.json()["id"]
    second_commit = await client.post(
        f"/account-data/{account.id}/imports/{second_batch_id}/commit",
        headers=_auth(operator_token),
    )
    assert second_commit.status_code == 200

    admin_revoke = await client.post(
        f"/account-data/{account.id}/imports/{second_batch_id}/revoke",
        headers=_auth(admin_token),
    )

    assert admin_revoke.status_code == 200
