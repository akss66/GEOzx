"""Platform integration API tests."""

import pytest
from sqlalchemy import select

from app.models import Account, Event, MetricSnapshot, PlatformAccountAuth, PlatformIntegration
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
async def test_member_can_list_but_not_configure_platform_integrations(client, member):
    token = await _token(client, "user@test.com", "user-pw-123")
    headers = _auth(token)

    listing = await client.get("/platform-integrations", headers=headers)
    assert listing.status_code == 200
    assert {row["platform"] for row in listing.json()} == {
        "douyin",
        "xiaohongshu",
        "shipinhao",
    }

    resp = await client.patch(
        "/platform-integrations/douyin",
        headers=headers,
        json={"status": "configured", "client_key": "nope"},
    )
    assert resp.status_code == 403


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
    assert body["scopes"] == ["user_info", "video.list"]
    assert "state=" in body["authorization_url"]
    assert "client_secret" not in body["authorization_url"]


@pytest.mark.asyncio
async def test_douyin_trial_whitelist_url_uses_template_scope(client, admin):
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
    assert body["scopes"] == ["trial.whitelist"]
    assert body["authorization_url"].startswith(
        "https://open.douyin.com/platform/oauth/connect/?"
    )
    assert "client_key=douyin-client-key" in body["authorization_url"]
    assert "scope=trial.whitelist" in body["authorization_url"]
    assert "redirect_uri=https%3A%2F%2Fconsole.example.com%2Fdouyin%2Fcallback" in body[
        "authorization_url"
    ]
    assert "state=" not in body["authorization_url"]
    assert "client_secret" not in body["authorization_url"]


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
    body = callback.json()
    assert body["account_id"] == account_id
    assert body["external_open_id"] == "open-id-1"
    assert body["auth_status"] == "authorized"
    assert body["token_configured"] is True
    assert "access_token" not in body

    auth = await session.scalar(
        select(PlatformAccountAuth).where(PlatformAccountAuth.account_id == account_id)
    )
    assert auth is not None
    assert auth.token_secret_ref == "vault://dyflow/douyin/accounts/open-id-1/access-token"
    assert auth.refresh_secret_ref == "vault://dyflow/douyin/accounts/open-id-1/refresh-token"
    assert auth.raw_profile == {"open_id": "open-id-1", "union_id": "union-id-1"}


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
    body = callback.json()
    assert body["external_open_id"] == "scan-open-id-1"
    assert body["auth_status"] == "authorized"
    assert body["token_configured"] is True

    account = await session.scalar(
        select(Account).where(Account.external_account_id == "scan-open-id-1")
    )
    assert account is not None
    assert account.nickname == "扫码接入抖音号"
    assert account.platform == Platform.DOUYIN
    assert account.integration_status == "connected"
    assert account.auth_status == "authorized"
    assert account.data_sync_status == "pending"
    assert body["account_id"] == account.id


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
        }

    monkeypatch.setenv("DOUYIN_CLIENT_SECRET", "secret-from-env")
    monkeypatch.setattr(api_module.settings, "douyin_oauth_worker_secret", "bridge-secret")
    monkeypatch.setattr(api_module, "exchange_douyin_access_token", fake_exchange)

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
async def test_account_integration_update_syncs_formal_account_auth(client, admin, session):
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
            "integration_status": "connected",
            "auth_status": "authorized",
            "data_sync_status": "healthy",
        },
    )

    assert updated.status_code == 200
    auth = await session.scalar(
        select(PlatformAccountAuth).where(PlatformAccountAuth.account_id == account_id)
    )
    assert auth is not None
    assert auth.platform == "douyin"
    assert auth.external_open_id == "open-id-1"
    assert auth.auth_status == "authorized"
    assert auth.data_sync_status == "healthy"


@pytest.mark.asyncio
async def test_douyin_sync_metrics_fetches_profile_and_writes_snapshots(
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

    async def fake_video_list(*, access_token, open_id, cursor=0, count=20):
        assert access_token == "access-token-from-env"
        assert open_id == "open-id-1"
        return {
            "list": [
                {
                    "item_id": "video-1",
                    "title": "从一句话，到一整套执行",
                    "create_time": 1783339200,
                    "statistics": {
                        "play_count": 1000,
                        "digg_count": 80,
                        "comment_count": 25,
                        "share_count": 10,
                    },
                }
            ]
        }

    monkeypatch.setenv(
        "DYFLOW_VAULT_DYFLOW_DOUYIN_ACCOUNTS_OPEN_ID_1_ACCESS_TOKEN",
        "access-token-from-env",
    )
    monkeypatch.setattr(api_module, "fetch_douyin_user_info", fake_user_info)
    monkeypatch.setattr(api_module, "fetch_douyin_video_list", fake_video_list)

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
        scopes=["user_info", "video.list"],
        token_secret_ref="vault://dyflow/douyin/accounts/open-id-1/access-token",
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
    assert body["data_sync_status"] == "healthy"
    assert body["profile_synced"] is True
    assert body["video_count"] == 1
    assert body["snapshot_count"] == 1

    await session.refresh(auth)
    assert auth.data_sync_status == "healthy"
    assert auth.raw_profile["nickname"] == "同舟行测试号"
    assert auth.last_sync_at is not None

    snapshots = (
        await session.scalars(
            select(MetricSnapshot).where(MetricSnapshot.account_id == account["id"])
        )
    ).all()
    assert len(snapshots) == 1
    assert snapshots[0].source == "douyin"
    assert snapshots[0].title == "从一句话，到一整套执行"
    assert snapshots[0].play == 1000
    assert snapshots[0].like_rate == 0.08


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
