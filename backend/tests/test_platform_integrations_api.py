"""Platform integration API tests."""

from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app.core.credential_crypto import decrypt_credential, encrypt_credential
from app.models import Account, Event, PlatformAccountAuth, PlatformIntegration
from app.models.enums import Platform

DEFAULT_DOUYIN_SECRET_REF = "vault://dyflow/douyin/client-secret"


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_admin_configures_douyin_integration_without_exposing_secret(
    client, admin, session
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    resp = await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "client_secret_ref": "vault://dyflow/douyin/client-secret",
            "redirect_uri": "https://console.example.com/douyin/callback",
            "js_sdk_domain": "https://console.example.com",
            "scopes": ["user_info", "video.list"],
            "capabilities": {
                "web_oauth": "enabled",
                "js_sdk_signature": "pending",
                "h5_share": "not_requested",
            },
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["platform"] == "douyin"
    assert body["status"] == "configured"
    assert body["client_key"] == "douyin-client-key"
    assert body["client_secret_configured"] is True
    assert "client_secret_ref" not in body

    row = await session.scalar(
        select(PlatformIntegration).where(PlatformIntegration.platform == "douyin")
    )
    assert row is not None
    assert row.client_secret_ref == "vault://dyflow/douyin/client-secret"

    event = await session.scalar(select(Event).where(Event.type == "platform.integration.updated"))
    assert event is not None
    assert event.payload["platform"] == "douyin"
    assert event.payload["client_secret_configured"] is True
    assert "client_secret_ref" not in event.payload


@pytest.mark.asyncio
async def test_complete_platform_config_auto_promotes_status(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    resp = await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "not_configured",
            "client_key": "douyin-client-key",
            "client_secret_ref": "vault://dyflow/douyin/client-secret",
            "redirect_uri": "https://console.example.com/douyin/callback",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "configured"
    assert body["auth_status"] == "unauthorized"


@pytest.mark.asyncio
async def test_member_cannot_read_or_configure_platform_integrations(client, member):
    token = await _token(client, "user@test.com", "user-pw-123")
    headers = _auth(token)

    listing = await client.get("/platform-integrations", headers=headers)
    detail = await client.get("/platform-integrations/douyin", headers=headers)
    assert listing.status_code == 403
    assert detail.status_code == 403

    resp = await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={"status": "configured", "client_key": "nope"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_member_cannot_authorize_or_sync_douyin_account(client, admin, member):
    admin_token = await _token(client, "admin@test.com", "admin-pw-123")
    admin_headers = _auth(admin_token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=admin_headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "redirect_uri": "https://console.example.com/douyin/callback",
        },
    )
    account_id = (
        await client.post(
            "/accounts",
            headers=admin_headers,
            json={"nickname": "管理员账号", "platform": "douyin"},
        )
    ).json()["id"]
    member_token = await _token(client, "user@test.com", "user-pw-123")
    member_headers = _auth(member_token)

    authorize = await client.post(
        "/platform-integrations/douyin/oauth/authorize",
        headers=member_headers,
        json={"account_id": account_id},
    )
    sync = await client.post(
        f"/platform-integrations/douyin/accounts/{account_id}/sync-metrics",
        headers=member_headers,
    )

    assert authorize.status_code == 403
    assert sync.status_code == 403


@pytest.mark.asyncio
async def test_douyin_authorize_url_uses_configured_app_and_signed_state(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "redirect_uri": "https://console.example.com/douyin/callback",
            "scopes": ["user_info", "video.list"],
        },
    )
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "Douyin OAuth account", "platform": "douyin"},
        )
    ).json()["id"]

    resp = await client.post(
        "/platform-integrations/douyin/oauth/authorize",
        headers=headers,
        json={"account_id": account_id},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["platform"] == "douyin"
    assert body["client_key"] == "douyin-client-key"
    assert body["redirect_uri"] == "https://console.example.com/douyin/callback"
    assert body["scopes"] == ["user_info"]
    assert "state=" in body["authorization_url"]
    assert "client_secret" not in body["authorization_url"]


@pytest.mark.asyncio
async def test_douyin_capability_status_separates_app_permission_and_account_authorization(
    client, admin, session
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "redirect_uri": "https://console.example.com/douyin/callback",
            "scopes": [
                "user_info",
                "h5.share",
                "open.get.ticket",
            ],
        },
    )
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "能力诊断账号", "platform": "douyin"},
        )
    ).json()["id"]
    session.add(
        PlatformAccountAuth(
            org_id=admin.org_id,
            account_id=account_id,
            platform="douyin",
            external_open_id="open-capabilities",
            auth_status="authorized",
            data_sync_status="pending",
            scopes=["user_info"],
        )
    )
    await session.commit()

    response = await client.get(
        f"/platform-integrations/douyin/accounts/{account_id}/capabilities",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    by_key = {item["key"]: item for item in body["capabilities"]}
    assert by_key["profile"]["status"] == "ready"
    assert "audience_insights" not in by_key
    assert by_key["h5_publish"]["status"] == "ready"
    assert by_key["posting_feedback"]["status"] == "needs_app_permission"
    assert body["next_recommended"] == "posting_feedback"


@pytest.mark.asyncio
async def test_douyin_incremental_authorization_requests_only_missing_supported_scopes(
    client, admin, session
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "redirect_uri": "https://console.example.com/douyin/callback",
            "scopes": [
                "user_info",
                "task.posting.create",
                "posting.behavior",
                "task.posting.user_verification",
            ],
        },
    )
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "增量授权账号", "platform": "douyin"},
        )
    ).json()["id"]
    session.add(
        PlatformAccountAuth(
            org_id=admin.org_id,
            account_id=account_id,
            platform="douyin",
            external_open_id="open-incremental",
            auth_status="authorized",
            data_sync_status="pending",
            scopes=["user_info"],
        )
    )
    await session.commit()

    response = await client.post(
        "/platform-integrations/douyin/oauth/incremental-authorize",
        headers=headers,
        json={"account_id": account_id, "capability_key": "posting_feedback"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scopes"] == ["posting.behavior"]
    assert parse_qs(urlparse(body["authorization_url"]).query)["scope"] == ["posting.behavior"]


@pytest.mark.asyncio
async def test_douyin_incremental_authorization_blocks_unapproved_app_capability(
    client, admin, session
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "redirect_uri": "https://console.example.com/douyin/callback",
            "scopes": ["user_info"],
        },
    )
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "权限缺失账号", "platform": "douyin"},
        )
    ).json()["id"]
    session.add(
        PlatformAccountAuth(
            org_id=admin.org_id,
            account_id=account_id,
            platform="douyin",
            external_open_id="open-missing-app-permission",
            auth_status="authorized",
            data_sync_status="pending",
            scopes=["user_info"],
        )
    )
    await session.commit()

    response = await client.post(
        "/platform-integrations/douyin/oauth/incremental-authorize",
        headers=headers,
        json={"account_id": account_id, "capability_key": "posting_feedback"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["missing_app_scopes"] == [
        "task.posting.create",
        "posting.behavior",
        "task.posting.user_verification",
    ]


@pytest.mark.asyncio
async def test_douyin_incremental_callback_preserves_previously_granted_scopes(
    client, admin, session, monkeypatch
):
    from app.api import platform_integrations as api_module

    async def fake_exchange(*, client_key, client_secret, code):
        return {
            "open_id": "open-preserve-scopes",
            "scope": "posting.behavior",
            "expires_in": 7200,
            "refresh_expires_in": 86400,
            "access_token": "incremental-access-token",
            "refresh_token": "incremental-refresh-token",
        }

    monkeypatch.setenv("DOUYIN_CLIENT_SECRET", "secret-from-env")
    monkeypatch.setattr(
        api_module.settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    monkeypatch.setattr(api_module, "exchange_douyin_access_token", fake_exchange)
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "redirect_uri": "https://console.example.com/douyin/callback",
            "scopes": [
                "user_info",
                "task.posting.create",
                "posting.behavior",
                "task.posting.user_verification",
            ],
        },
    )
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "保留权限账号", "platform": "douyin"},
        )
    ).json()["id"]
    auth = PlatformAccountAuth(
        org_id=admin.org_id,
        account_id=account_id,
        platform="douyin",
        external_open_id="open-preserve-scopes",
        auth_status="authorized",
        data_sync_status="pending",
        scopes=["fans.data.bind", "user_info"],
    )
    session.add(auth)
    await session.commit()
    authorize = await client.post(
        "/platform-integrations/douyin/oauth/incremental-authorize",
        headers=headers,
        json={"account_id": account_id, "capability_key": "posting_feedback"},
    )

    callback = await client.get(
        "/platform-integrations/douyin/oauth/callback",
        params={"code": "incremental-code", "state": authorize.json()["state"]},
    )

    assert callback.status_code == 200
    await session.refresh(auth)
    assert auth.scopes == ["fans.data.bind", "posting.behavior", "user_info"]


@pytest.mark.asyncio
async def test_douyin_trial_whitelist_url_includes_required_user_info_scope(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "redirect_uri": "https://console.example.com/douyin/callback",
        },
    )

    resp = await client.post(
        "/platform-integrations/douyin/oauth/trial-whitelist",
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["scopes"] == ["trial.whitelist", "user_info"]
    assert body["authorization_url"].startswith(
        "https://open.douyin.com/platform/oauth/connect/?"
    )
    assert "client_key=douyin-client-key" in body["authorization_url"]
    assert "scope=trial.whitelist%2Cuser_info" in body["authorization_url"]
    assert "redirect_uri=https%3A%2F%2Fconsole.example.com%2Fdouyin%2Fcallback" in body[
        "authorization_url"
    ]
    query = parse_qs(urlparse(body["authorization_url"]).query)
    assert query["state"][0]
    assert "client_secret" not in body["authorization_url"]


@pytest.mark.asyncio
async def test_douyin_trial_whitelist_callback_exchanges_code(client, admin, session, monkeypatch):
    from app.api import platform_integrations as api_module

    async def fake_exchange(*, client_key, client_secret, code):
        assert client_key == "douyin-client-key"
        assert client_secret == "secret-from-env"
        assert code == "trial-code"
        return {
            "open_id": "trial-open-id",
            "scope": "user_info,trial.whitelist",
            "access_token": "trial-access-token",
            "refresh_token": "trial-refresh-token",
        }

    monkeypatch.setenv("DOUYIN_CLIENT_SECRET", "secret-from-env")
    monkeypatch.setattr(api_module, "exchange_douyin_access_token", fake_exchange)
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "redirect_uri": "https://console.example.com/douyin/callback",
        },
    )
    authorize = await client.post(
        "/platform-integrations/douyin/oauth/trial-whitelist",
        headers=headers,
    )
    state = parse_qs(urlparse(authorize.json()["authorization_url"]).query)["state"][0]

    response = await client.get(
        "/platform-integrations/douyin/oauth/callback",
        params={
            "code": "trial-code",
            "state": state,
            "scopes": "user_info,trial.whitelist",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "测试白名单已完成" in response.text
    event = await session.scalar(
        select(Event).where(Event.type == "platform.douyin.trial_whitelist.authorized")
    )
    assert event is not None
    assert event.payload["external_open_id"] == "trial-open-id"
    assert event.payload["scopes"] == ["user_info", "trial.whitelist"]


@pytest.mark.asyncio
async def test_douyin_oauth_callback_stores_account_auth_without_token_leak(
    client, admin, session, monkeypatch
):
    from app.api import platform_integrations as api_module

    async def fake_exchange(*, client_key, client_secret, code):
        assert client_key == "douyin-client-key"
        assert client_secret == "secret-from-env"
        assert code == "auth-code"
        return {
            "open_id": "open-id-1",
            "union_id": "union-id-1",
            "scope": "user_info,video.list",
            "expires_in": 7200,
            "refresh_expires_in": 86400,
            "access_token": "never-return-this",
            "refresh_token": "never-return-this-either",
        }

    async def fake_user_info(*, access_token, open_id):
        assert access_token == "never-return-this"
        assert open_id == "open-id-1"
        return {
            "open_id": "open-id-1",
            "union_id": "union-id-1",
            "nickname": "Real Douyin nickname",
            "avatar": "https://example.com/douyin-avatar.png",
        }

    monkeypatch.setenv("DOUYIN_CLIENT_SECRET", "secret-from-env")
    monkeypatch.setattr(
        api_module.settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    monkeypatch.setattr(api_module, "exchange_douyin_access_token", fake_exchange)
    monkeypatch.setattr(api_module, "fetch_douyin_user_info", fake_user_info)

    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "redirect_uri": "https://console.example.com/douyin/callback",
            "client_secret_ref": DEFAULT_DOUYIN_SECRET_REF,
            "scopes": ["user_info", "video.list"],
        },
    )
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "Douyin OAuth account", "platform": "douyin"},
        )
    ).json()["id"]
    state = (
        await client.post(
            "/platform-integrations/douyin/oauth/authorize",
            headers=headers,
            json={"account_id": account_id},
        )
    ).json()["state"]

    callback = await client.get(
        "/platform-integrations/douyin/oauth/callback",
        params={"code": "auth-code", "state": state},
    )

    assert callback.status_code == 200
    assert callback.headers["content-type"].startswith("text/html")
    assert "抖音账号授权成功" in callback.text
    assert "返回账号矩阵" in callback.text
    assert "never-return-this" not in callback.text

    auth = await session.scalar(
        select(PlatformAccountAuth).where(PlatformAccountAuth.account_id == account_id)
    )
    assert auth is not None
    assert auth.token_secret_ref is None
    assert auth.refresh_secret_ref is None
    assert auth.access_token_encrypted is not None
    assert auth.refresh_token_encrypted is not None
    assert "never-return-this" not in auth.access_token_encrypted
    assert "never-return-this-either" not in auth.refresh_token_encrypted
    assert decrypt_credential(auth.access_token_encrypted) == "never-return-this"
    assert decrypt_credential(auth.refresh_token_encrypted) == "never-return-this-either"
    assert auth.raw_profile["nickname"] == "Real Douyin nickname"

    account = await session.get(Account, account_id)
    assert account is not None
    assert account.nickname == "Real Douyin nickname"
    assert account.auth["avatar"] == "https://example.com/douyin-avatar.png"


@pytest.mark.asyncio
async def test_douyin_scan_add_oauth_callback_creates_matrix_account(
    client, admin, session, monkeypatch
):
    from app.api import platform_integrations as api_module

    async def fake_exchange(*, client_key, client_secret, code):
        assert client_key == "douyin-client-key"
        assert client_secret == "secret-from-env"
        assert code == "scan-code"
        return {
            "open_id": "scan-open-id-1",
            "union_id": "scan-union-id-1",
            "scope": "user_info",
            "expires_in": 7200,
            "refresh_expires_in": 86400,
            "access_token": "scan-access-token",
            "refresh_token": "scan-refresh-token",
        }

    async def fake_user_info(*, access_token, open_id):
        assert access_token == "scan-access-token"
        return {"open_id": open_id}

    monkeypatch.setenv("DOUYIN_CLIENT_SECRET", "secret-from-env")
    monkeypatch.setattr(
        api_module.settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    monkeypatch.setattr(api_module, "exchange_douyin_access_token", fake_exchange)
    monkeypatch.setattr(api_module, "fetch_douyin_user_info", fake_user_info)

    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "redirect_uri": "https://console.example.com/douyin/callback",
            "client_secret_ref": DEFAULT_DOUYIN_SECRET_REF,
            "scopes": ["user_info"],
        },
    )
    state = (
        await client.post(
            "/platform-integrations/douyin/oauth/scan-add",
            headers=headers,
            json={"nickname": "扫码接入抖音号"},
        )
    ).json()["state"]

    callback = await client.get(
        "/platform-integrations/douyin/oauth/callback",
        params={"code": "scan-code", "state": state},
    )

    assert callback.status_code == 200
    assert callback.headers["content-type"].startswith("text/html")
    assert "抖音账号添加成功" in callback.text
    assert "返回账号矩阵" in callback.text
    assert "scan-access-token" not in callback.text

    account = await session.scalar(
        select(Account).where(Account.external_account_id == "scan-open-id-1")
    )
    assert account is not None
    assert account.nickname == "扫码接入抖音号"
    assert account.platform == Platform.DOUYIN
    assert account.integration_status == "connected"
    assert account.auth_status == "authorized"
    assert account.data_sync_status == "pending"
    auth = await session.scalar(
        select(PlatformAccountAuth).where(PlatformAccountAuth.account_id == account.id)
    )
    assert auth is not None
    assert auth.auth_status == "authorized"
    assert auth.token_configured is True


@pytest.mark.asyncio
async def test_douyin_worker_complete_requires_bridge_secret(client, admin, monkeypatch):
    from app.api import platform_integrations as api_module

    monkeypatch.setattr(api_module.settings, "douyin_oauth_worker_secret", "")

    resp = await client.post(
        "/platform-integrations/douyin/oauth/complete",
        json={"code": "scan-code", "state": "signed-state"},
    )

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_douyin_worker_complete_rejects_invalid_bridge_secret(client, admin, monkeypatch):
    from app.api import platform_integrations as api_module

    monkeypatch.setattr(api_module.settings, "douyin_oauth_worker_secret", "bridge-secret")

    resp = await client.post(
        "/platform-integrations/douyin/oauth/complete",
        headers={"X-Dyflow-Worker-Secret": "wrong-secret"},
        json={"code": "scan-code", "state": "signed-state"},
    )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_douyin_worker_complete_accepts_authorization_bearer(client, admin, monkeypatch):
    from app.api import platform_integrations as api_module

    monkeypatch.setattr(api_module.settings, "douyin_oauth_worker_secret", "bridge-secret")

    resp = await client.post(
        "/platform-integrations/douyin/oauth/complete",
        headers={"Authorization": "Bearer bridge-secret"},
        json={"code": "scan-code", "state": "not-a-valid-state"},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid state"


@pytest.mark.asyncio
async def test_douyin_worker_complete_creates_scan_account_with_bridge_secret(
    client, admin, session, monkeypatch
):
    from app.api import platform_integrations as api_module

    async def fake_exchange(*, client_key, client_secret, code):
        assert client_key == "douyin-client-key"
        assert client_secret == "secret-from-env"
        assert code == "scan-code"
        return {
            "open_id": "worker-open-id-1",
            "union_id": "worker-union-id-1",
            "scope": "user_info",
            "expires_in": 7200,
            "refresh_expires_in": 86400,
            "access_token": "worker-access-token",
            "refresh_token": "worker-refresh-token",
        }

    async def fake_user_info(*, access_token, open_id):
        assert access_token == "worker-access-token"
        return {"open_id": open_id}

    monkeypatch.setenv("DOUYIN_CLIENT_SECRET", "secret-from-env")
    monkeypatch.setattr(api_module.settings, "douyin_oauth_worker_secret", "bridge-secret")
    monkeypatch.setattr(
        api_module.settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    monkeypatch.setattr(api_module, "exchange_douyin_access_token", fake_exchange)
    monkeypatch.setattr(api_module, "fetch_douyin_user_info", fake_user_info)

    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "redirect_uri": "https://dyflow-douyin-callback.example.workers.dev/platform-integrations/douyin/oauth/callback",
            "client_secret_ref": DEFAULT_DOUYIN_SECRET_REF,
            "scopes": ["user_info"],
        },
    )
    state = (
        await client.post(
            "/platform-integrations/douyin/oauth/scan-add",
            headers=headers,
            json={"nickname": "Worker scan account"},
        )
    ).json()["state"]

    callback = await client.post(
        "/platform-integrations/douyin/oauth/complete",
        headers={"X-Dyflow-Worker-Secret": "bridge-secret"},
        json={"code": "scan-code", "state": state},
    )

    assert callback.status_code == 200
    body = callback.json()
    assert body["external_open_id"] == "worker-open-id-1"
    assert body["auth_status"] == "authorized"
    assert body["token_configured"] is True

    account = await session.scalar(
        select(Account).where(Account.external_account_id == "worker-open-id-1")
    )
    assert account is not None
    assert account.nickname == "Worker scan account"
    assert account.platform == Platform.DOUYIN


@pytest.mark.asyncio
async def test_douyin_js_signature_uses_server_side_ticket(client, admin, monkeypatch):
    from app.api import platform_integrations as api_module

    async def fake_ticket(*, integration, client_secret):
        assert integration.platform == Platform.DOUYIN.value
        assert client_secret == "secret-from-env"
        return "ticket-value"

    monkeypatch.setenv("DOUYIN_CLIENT_SECRET", "secret-from-env")
    monkeypatch.setattr(api_module, "get_douyin_jsb_ticket", fake_ticket)

    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "client_secret_ref": DEFAULT_DOUYIN_SECRET_REF,
            "js_sdk_domain": "https://console.example.com",
        },
    )

    resp = await client.post(
        "/platform-integrations/douyin/js-signature",
        headers=headers,
        json={"url": "https://console.example.com/path?a=1#section"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["client_key"] == "douyin-client-key"
    assert body["url"] == "https://console.example.com/path?a=1"
    assert body["nonce_str"]
    assert body["timestamp"] > 0
    assert len(body["signature"]) == 32
    assert "ticket-value" not in body.values()


@pytest.mark.asyncio
async def test_manual_account_integration_update_syncs_formal_account_auth(
    client, admin, session
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={
                "nickname": "Douyin OAuth account",
                "platform": "douyin",
                "external_account_id": "open-id-1",
            },
        )
    ).json()["id"]

    updated = await client.patch(
        f"/accounts/{account_id}/integration",
        headers=headers,
        json={
            "integration_status": "manual",
            "auth_status": "manual",
            "data_sync_status": "manual",
        },
    )

    assert updated.status_code == 200
    auth = await session.scalar(
        select(PlatformAccountAuth).where(PlatformAccountAuth.account_id == account_id)
    )
    assert auth is not None
    assert auth.platform == "douyin"
    assert auth.external_open_id == "open-id-1"
    assert auth.auth_status == "manual"
    assert auth.data_sync_status == "manual"


@pytest.mark.asyncio
async def test_douyin_sync_updates_profile_without_calling_retired_video_list(
    client, admin, session, monkeypatch
):
    from app.api import platform_integrations as api_module

    async def fake_user_info(*, access_token, open_id):
        assert access_token == "access-token-from-env"
        assert open_id == "open-id-1"
        return {
            "open_id": "open-id-1",
            "union_id": "union-id-1",
            "nickname": "同舟行测试号",
            "avatar": "https://example.com/avatar.png",
        }

    async def retired_video_list_must_not_run(**kwargs):
        raise AssertionError("retired /video/list endpoint must not be called")

    monkeypatch.setattr(
        api_module.settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    monkeypatch.setattr(api_module, "fetch_douyin_user_info", fake_user_info)
    monkeypatch.setattr(
        api_module,
        "fetch_douyin_video_list",
        retired_video_list_must_not_run,
        raising=False,
    )

    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "Old nickname", "platform": "douyin"},
        )
    ).json()
    auth = PlatformAccountAuth(
        org_id=admin.org_id,
        account_id=account["id"],
        platform="douyin",
        external_open_id="open-id-1",
        union_id="union-id-1",
        auth_status="authorized",
        data_sync_status="pending",
        scopes=["user_info"],
        access_token_encrypted=encrypt_credential("access-token-from-env"),
    )
    session.add(auth)
    await session.commit()

    resp = await client.post(
        f"/platform-integrations/douyin/accounts/{account['id']}/sync-metrics",
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["account_id"] == account["id"]
    assert body["platform"] == "douyin"
    assert body["data_sync_status"] == "pending"
    assert body["profile_synced"] is True
    assert body["video_count"] == 0
    assert body["snapshot_count"] == 0

    await session.refresh(auth)
    assert auth.data_sync_status == "pending"
    assert auth.raw_profile["nickname"] == "同舟行测试号"
    assert auth.last_sync_at is not None
    refreshed_account = await session.get(Account, account["id"])
    assert refreshed_account is not None
    assert refreshed_account.auth["metrics_sync_mode"] == "posting_task_required"


@pytest.mark.asyncio
async def test_douyin_sync_refreshes_expired_encrypted_access_token(
    client, admin, session, monkeypatch
):
    from datetime import UTC, datetime, timedelta

    from app.api import platform_integrations as api_module

    async def fake_refresh(*, client_key, refresh_token):
        assert client_key == "douyin-client-key"
        assert refresh_token == "old-refresh-token"
        return {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 7200,
            "refresh_expires_in": 86400,
        }

    async def fake_user_info(*, access_token, open_id):
        assert access_token == "new-access-token"
        return {"open_id": open_id, "nickname": "Refreshed account"}

    monkeypatch.setattr(
        api_module.settings,
        "credential_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    monkeypatch.setattr(api_module, "refresh_douyin_access_token", fake_refresh)
    monkeypatch.setattr(api_module, "fetch_douyin_user_info", fake_user_info)

    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={
            "status": "configured",
            "client_key": "douyin-client-key",
            "client_secret_ref": DEFAULT_DOUYIN_SECRET_REF,
            "redirect_uri": "https://tzxai.top/platform-integrations/douyin/oauth/callback",
        },
    )
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "Expired token account", "platform": "douyin"},
        )
    ).json()
    auth = PlatformAccountAuth(
        org_id=admin.org_id,
        account_id=account["id"],
        platform="douyin",
        external_open_id="open-id-refresh",
        auth_status="authorized",
        data_sync_status="pending",
        scopes=["user_info"],
        access_token_encrypted=encrypt_credential("old-access-token"),
        refresh_token_encrypted=encrypt_credential("old-refresh-token"),
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
        refresh_expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(auth)
    await session.commit()

    resp = await client.post(
        f"/platform-integrations/douyin/accounts/{account['id']}/sync-metrics",
        headers=headers,
    )

    assert resp.status_code == 200
    await session.refresh(auth)
    assert decrypt_credential(auth.access_token_encrypted) == "new-access-token"
    assert decrypt_credential(auth.refresh_token_encrypted) == "new-refresh-token"
    token_expires_at = auth.token_expires_at
    assert token_expires_at is not None
    if token_expires_at.tzinfo is None:
        token_expires_at = token_expires_at.replace(tzinfo=UTC)
    assert token_expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_douyin_sync_metrics_requires_resolvable_account_token(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "No token account", "platform": "douyin"},
        )
    ).json()
    session.add(
        PlatformAccountAuth(
            org_id=admin.org_id,
            account_id=account["id"],
            platform="douyin",
            external_open_id="open-id-missing",
            auth_status="authorized",
            data_sync_status="pending",
            scopes=["user_info", "video.list"],
            token_secret_ref="vault://dyflow/douyin/accounts/open-id-missing/access-token",
        )
    )
    await session.commit()

    resp = await client.post(
        f"/platform-integrations/douyin/accounts/{account['id']}/sync-metrics",
        headers=headers,
    )

    assert resp.status_code == 409
    assert "account access token" in resp.json()["detail"]
