"""WeChat Open Platform authorization and encrypted callback API tests."""

from __future__ import annotations

import base64
import hashlib
import struct
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import HTTPException
from sqlalchemy import func, select

import app.api.platform_integrations as platform_api
from app.config import settings
from app.core.credential_crypto import decrypt_credential, encrypt_credential
from app.models import (
    Account,
    Event,
    Org,
    PlatformAccountAuth,
    PlatformIntegration,
    WechatComponentCredential,
)
from app.models.enums import Platform
from app.schemas.platform import WechatAuthorizationGrant
from app.services.wechat_component import WechatOpenPlatformClient

_VERIFY_TOKEN = "wechat-verify-token"
_AES_KEY = b"0123456789abcdef0123456789abcdef"
_ENCODING_AES_KEY = base64.b64encode(_AES_KEY).decode("ascii").rstrip("=")
_COMPONENT_APPID = "wx_component_appid"


async def _token(client) -> str:
    response = await client.post(
        "/auth/login",
        json={"email": "admin@test.com", "password": "admin-pw-123"},
    )
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_integration(session, *, org_id: int) -> PlatformIntegration:
    integration = PlatformIntegration(
        org_id=org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT.value,
        status="configured",
        client_key=_COMPONENT_APPID,
        client_secret_ref="env:WECHAT_COMPONENT_APP_SECRET",
        redirect_uri="https://api.example.com/platform-integrations/wechat/oauth/callback",
        auth_status="unauthorized",
    )
    session.add(integration)
    await session.flush()
    session.add(
        WechatComponentCredential(platform_integration_id=integration.id)
    )
    await session.commit()
    return integration


def _stub_authorization_dependencies(monkeypatch, *, pre_auth_code: str = "pre-auth-code"):
    async def component_token(_self, _session, _integration_id):
        return "component-access-token"

    async def pre_auth(_component_appid: str, _component_access_token: str):
        return pre_auth_code

    monkeypatch.setattr(
        WechatOpenPlatformClient,
        "get_component_access_token",
        component_token,
    )
    monkeypatch.setattr(
        platform_api,
        "_request_wechat_pre_auth_code",
        pre_auth,
        raising=False,
    )


async def _create_authorization_session(client, session, admin, monkeypatch) -> dict:
    await _seed_integration(session, org_id=admin.org_id)
    _stub_authorization_dependencies(monkeypatch)
    response = await client.post(
        "/platform-integrations/wechat/authorization-sessions",
        headers=_auth(await _token(client)),
        json={"knowledge_base_id": 12},
    )
    assert response.status_code == 201
    return response.json()


def _state_from_authorization_url(url: str) -> str:
    outer = parse_qs(urlparse(url).query)
    callback_url = outer["redirect_uri"][0]
    return parse_qs(urlparse(callback_url).query)["state"][0]


def _encrypted_event(
    inner_xml: str,
    *,
    component_appid: str = _COMPONENT_APPID,
    timestamp: int | None = None,
    nonce: str = "event-nonce",
) -> tuple[bytes, dict[str, str]]:
    timestamp = timestamp or int(datetime.now(UTC).timestamp())
    message = inner_xml.encode("utf-8")
    plaintext = (
        b"0123456789abcdef"
        + struct.pack("!I", len(message))
        + message
        + component_appid.encode("utf-8")
    )
    pad = 32 - (len(plaintext) % 32)
    plaintext += bytes([pad]) * pad
    encryptor = Cipher(
        algorithms.AES(_AES_KEY), modes.CBC(_AES_KEY[:16])
    ).encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    encrypted = base64.b64encode(ciphertext).decode("ascii")
    signature = hashlib.sha1(
        "".join(sorted((_VERIFY_TOKEN, str(timestamp), nonce, encrypted))).encode()
    ).hexdigest()
    outer_xml = (
        "<xml><AppId><![CDATA["
        + component_appid
        + "]]></AppId><Encrypt><![CDATA["
        + encrypted
        + "]]></Encrypt></xml>"
    )
    return outer_xml.encode(), {
        "msg_signature": signature,
        "timestamp": str(timestamp),
        "nonce": nonce,
    }


def _configure_callback_secrets(monkeypatch) -> None:
    monkeypatch.setenv("WECHAT_COMPONENT_VERIFY_TOKEN", _VERIFY_TOKEN)
    monkeypatch.setenv("WECHAT_COMPONENT_ENCODING_AES_KEY", _ENCODING_AES_KEY)
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())


@pytest.mark.asyncio
async def test_create_authorization_session_returns_official_url_and_hashes_state(
    client, session, admin, monkeypatch
):
    result = await _create_authorization_session(
        client, session, admin, monkeypatch
    )

    assert result["state_id"]
    assert result["expires_at"]
    assert "pre_auth_code=pre-auth-code" in result["authorization_url"]
    assert "component_access_token" not in result["authorization_url"]
    raw_state = _state_from_authorization_url(result["authorization_url"])
    assert len(raw_state) >= 32

    created = await session.scalar(
        select(Event).where(Event.type == "wechat.authorization.session.created")
    )
    assert created is not None
    assert created.org_id == admin.org_id
    assert created.payload["initiated_by_id"] == admin.id
    assert created.payload["knowledge_base_id"] == 12
    assert raw_state not in str(created.payload)
    assert created.idempotency_key == hashlib.sha256(raw_state.encode()).hexdigest()


@pytest.mark.asyncio
async def test_authorization_state_is_consumed_once_and_credentials_are_encrypted(
    client, session, admin, monkeypatch
):
    _configure_callback_secrets(monkeypatch)
    result = await _create_authorization_session(
        client, session, admin, monkeypatch
    )
    raw_state = _state_from_authorization_url(result["authorization_url"])
    calls = 0

    async def exchange(_self, _session, _integration_id, authorization_code):
        nonlocal calls
        calls += 1
        assert authorization_code == "one-time-code"
        return WechatAuthorizationGrant(
            authorizer_appid="wx_authorizer_123456",
            authorizer_access_token="authorizer-access-token",
            authorizer_refresh_token="authorizer-refresh-token",
            expires_in=7200,
            func_info=[15, 1, 15],
        )

    monkeypatch.setattr(
        WechatOpenPlatformClient,
        "exchange_authorization_code",
        exchange,
    )

    callback = await client.get(
        "/platform-integrations/wechat/oauth/callback",
        params={"state": raw_state, "auth_code": "one-time-code"},
        follow_redirects=False,
    )
    replay = await client.get(
        "/platform-integrations/wechat/oauth/callback",
        params={"state": raw_state, "authorization_code": "one-time-code"},
        follow_redirects=False,
    )

    assert callback.status_code == 307
    assert callback.headers["location"] == "/accounts?wechat_authorization=success"
    assert "token" not in callback.headers["location"]
    assert replay.status_code == 400
    assert calls == 1
    account = await session.scalar(
        select(Account).where(
            Account.org_id == admin.org_id,
            Account.platform == Platform.WECHAT_OFFICIAL_ACCOUNT,
        )
    )
    assert account is not None
    auth = await session.scalar(
        select(PlatformAccountAuth).where(PlatformAccountAuth.account_id == account.id)
    )
    assert auth is not None
    assert auth.external_open_id == "wx_authorizer_123456"
    assert auth.auth_status == "authorized"
    assert auth.scopes == ["1", "15"]
    assert auth.access_token_encrypted != "authorizer-access-token"
    assert auth.refresh_token_encrypted != "authorizer-refresh-token"
    assert decrypt_credential(auth.access_token_encrypted or "") == "authorizer-access-token"
    assert decrypt_credential(auth.refresh_token_encrypted or "") == "authorizer-refresh-token"


@pytest.mark.asyncio
async def test_expired_authorization_state_is_rejected_before_code_exchange(
    client, session, admin, monkeypatch
):
    result = await _create_authorization_session(client, session, admin, monkeypatch)
    raw_state = _state_from_authorization_url(result["authorization_url"])
    created = await session.scalar(
        select(Event).where(Event.type == "wechat.authorization.session.created")
    )
    assert created is not None
    created.payload = {
        **(created.payload or {}),
        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }
    await session.commit()

    async def exchange(*_args, **_kwargs):
        raise AssertionError("expired state must not exchange an authorization code")

    monkeypatch.setattr(
        WechatOpenPlatformClient,
        "exchange_authorization_code",
        exchange,
    )
    response = await client.get(
        "/platform-integrations/wechat/oauth/callback",
        params={"state": raw_state, "authorization_code": "unused-code"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_invalid_callback_signature_is_rejected(client, monkeypatch):
    _configure_callback_secrets(monkeypatch)
    response = await client.post(
        "/platform-integrations/wechat/events?msg_signature=bad&timestamp=1&nonce=n",
        content="<xml />",
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_encrypted_ticket_callback_validates_and_deduplicates(
    client, session, admin, monkeypatch
):
    _configure_callback_secrets(monkeypatch)
    integration = await _seed_integration(session, org_id=admin.org_id)
    body, params = _encrypted_event(
        "<xml><AppId><![CDATA[wx_component_appid]]></AppId>"
        "<CreateTime>1786410000</CreateTime>"
        "<InfoType><![CDATA[component_verify_ticket]]></InfoType>"
        "<ComponentVerifyTicket><![CDATA[secret-ticket]]>"
        "</ComponentVerifyTicket></xml>"
    )

    first = await client.post(
        "/platform-integrations/wechat/events", params=params, content=body
    )
    second = await client.post(
        "/platform-integrations/wechat/events", params=params, content=body
    )

    assert first.status_code == 200
    assert first.text == "success"
    assert second.status_code == 200
    credential = await session.scalar(
        select(WechatComponentCredential).where(
            WechatComponentCredential.platform_integration_id == integration.id
        )
    )
    assert credential is not None
    assert decrypt_credential(credential.component_verify_ticket_encrypted or "") == (
        "secret-ticket"
    )
    events = (
        await session.scalars(
            select(Event).where(Event.type == "wechat.component_verify_ticket")
        )
    ).all()
    assert len(events) == 1
    assert events[0].payload == {
        "component_appid": _COMPONENT_APPID,
        "create_time": 1786410000,
        "info_type": "component_verify_ticket",
    }


@pytest.mark.asyncio
async def test_component_ticket_updates_each_org_using_the_single_component(
    client, session, admin, monkeypatch
):
    _configure_callback_secrets(monkeypatch)
    first = await _seed_integration(session, org_id=admin.org_id)
    second_org = Org(name="Second WeChat tenant")
    session.add(second_org)
    await session.commit()
    second = await _seed_integration(session, org_id=second_org.id)
    body, params = _encrypted_event(
        "<xml><AppId><![CDATA[wx_component_appid]]></AppId>"
        "<CreateTime>1786410001</CreateTime>"
        "<InfoType><![CDATA[component_verify_ticket]]></InfoType>"
        "<ComponentVerifyTicket><![CDATA[shared-component-ticket]]>"
        "</ComponentVerifyTicket></xml>"
    )

    response = await client.post(
        "/platform-integrations/wechat/events", params=params, content=body
    )

    assert response.status_code == 200
    credentials = (
        await session.scalars(
            select(WechatComponentCredential).where(
                WechatComponentCredential.platform_integration_id.in_(
                    [first.id, second.id]
                )
            )
        )
    ).all()
    assert len(credentials) == 2
    assert {
        decrypt_credential(item.component_verify_ticket_encrypted or "")
        for item in credentials
    } == {"shared-component-ticket"}


@pytest.mark.asyncio
async def test_event_rejects_stale_timestamp_and_wrong_appid(
    client, session, admin, monkeypatch
):
    _configure_callback_secrets(monkeypatch)
    await _seed_integration(session, org_id=admin.org_id)
    inner = (
        "<xml><InfoType><![CDATA[component_verify_ticket]]></InfoType>"
        "<ComponentVerifyTicket><![CDATA[secret-ticket]]>"
        "</ComponentVerifyTicket></xml>"
    )
    stale_body, stale_params = _encrypted_event(inner, timestamp=1)
    wrong_body, wrong_params = _encrypted_event(
        inner, component_appid="wx_wrong_component"
    )

    stale = await client.post(
        "/platform-integrations/wechat/events",
        params=stale_params,
        content=stale_body,
    )
    wrong = await client.post(
        "/platform-integrations/wechat/events",
        params=wrong_params,
        content=wrong_body,
    )

    assert stale.status_code == 401
    assert wrong.status_code == 401
    assert await session.scalar(
        select(func.count(Event.id)).where(Event.type == "wechat.component_verify_ticket")
    ) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("info_type", ["authorized", "updateauthorized"])
async def test_authorization_lifecycle_event_is_deduplicated_without_granting_access(
    client, session, admin, monkeypatch, info_type
):
    _configure_callback_secrets(monkeypatch)
    await _seed_integration(session, org_id=admin.org_id)
    body, params = _encrypted_event(
        "<xml><AppId><![CDATA[wx_component_appid]]></AppId>"
        "<CreateTime>1786410005</CreateTime>"
        f"<InfoType><![CDATA[{info_type}]]></InfoType>"
        "<AuthorizerAppid><![CDATA[wx_authorizer_pending]]></AuthorizerAppid>"
        "<AuthorizationCode><![CDATA[temporary-secret-code]]>"
        "</AuthorizationCode></xml>"
    )

    first = await client.post(
        "/platform-integrations/wechat/events", params=params, content=body
    )
    second = await client.post(
        "/platform-integrations/wechat/events", params=params, content=body
    )

    assert first.status_code == 200
    assert second.status_code == 200
    events = (
        await session.scalars(select(Event).where(Event.type == f"wechat.{info_type}"))
    ).all()
    assert len(events) == 1
    assert "temporary-secret-code" not in str(events[0].payload)
    assert await session.scalar(
        select(func.count(PlatformAccountAuth.id)).where(
            PlatformAccountAuth.external_open_id == "wx_authorizer_pending"
        )
    ) == 0


@pytest.mark.asyncio
async def test_unauthorized_event_revokes_wechat_without_mutating_douyin(
    client, session, admin, monkeypatch
):
    _configure_callback_secrets(monkeypatch)
    await _seed_integration(session, org_id=admin.org_id)
    wechat_account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="WeChat account",
        external_account_id="wx_authorizer_revoke",
        auth={"auth_status": "authorized", "integration_status": "connected"},
    )
    douyin_account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Douyin account",
    )
    session.add_all((wechat_account, douyin_account))
    await session.flush()
    wechat_auth = PlatformAccountAuth(
        org_id=admin.org_id,
        account_id=wechat_account.id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT.value,
        external_open_id="wx_authorizer_revoke",
        auth_status="authorized",
        access_token_encrypted=encrypt_credential("wechat-access"),
        refresh_token_encrypted=encrypt_credential("wechat-refresh"),
    )
    douyin_auth = PlatformAccountAuth(
        org_id=admin.org_id,
        account_id=douyin_account.id,
        platform=Platform.DOUYIN.value,
        external_open_id="douyin-open-id",
        auth_status="authorized",
        access_token_encrypted=encrypt_credential("douyin-access"),
    )
    session.add_all((wechat_auth, douyin_auth))
    await session.commit()
    body, params = _encrypted_event(
        "<xml><AppId><![CDATA[wx_component_appid]]></AppId>"
        "<CreateTime>1786410010</CreateTime>"
        "<InfoType><![CDATA[unauthorized]]></InfoType>"
        "<AuthorizerAppid><![CDATA[wx_authorizer_revoke]]>"
        "</AuthorizerAppid></xml>"
    )

    first = await client.post(
        "/platform-integrations/wechat/events", params=params, content=body
    )
    second = await client.post(
        "/platform-integrations/wechat/events", params=params, content=body
    )

    assert first.status_code == 200
    assert second.status_code == 200
    await session.refresh(wechat_auth)
    await session.refresh(douyin_auth)
    assert wechat_auth.auth_status == "unauthorized"
    assert wechat_auth.access_token_encrypted is None
    assert wechat_auth.refresh_token_encrypted is None
    assert douyin_auth.auth_status == "authorized"
    assert decrypt_credential(douyin_auth.access_token_encrypted or "") == "douyin-access"
    assert await session.scalar(
        select(func.count(Event.id)).where(Event.type == "wechat.unauthorized")
    ) == 1


@pytest.mark.asyncio
async def test_callback_config_is_required_and_event_body_is_bounded(
    client, monkeypatch
):
    monkeypatch.delenv("WECHAT_COMPONENT_VERIFY_TOKEN", raising=False)
    monkeypatch.delenv("WECHAT_COMPONENT_ENCODING_AES_KEY", raising=False)
    missing_config = await client.post(
        "/platform-integrations/wechat/events",
        params={"msg_signature": "a" * 40, "timestamp": "1", "nonce": "n"},
        content="<xml />",
    )

    _configure_callback_secrets(monkeypatch)
    oversized = await client.post(
        "/platform-integrations/wechat/events",
        params={"msg_signature": "a" * 40, "timestamp": "1", "nonce": "n"},
        content=b"x" * 65537,
    )

    assert missing_config.status_code == 503
    assert oversized.status_code == 413


@pytest.mark.asyncio
async def test_chunked_callback_is_rejected_before_buffering_the_whole_body(monkeypatch):
    _configure_callback_secrets(monkeypatch)

    class ChunkedRequest:
        headers: dict[str, str] = {}

        async def body(self):
            raise AssertionError("callback route must not buffer an unbounded body")

        async def stream(self):
            yield b"x" * 40_000
            yield b"y" * 40_000

    with pytest.raises(HTTPException) as captured:
        await platform_api.handle_wechat_encrypted_event(
            request=ChunkedRequest(),
            session=None,
            msg_signature="a" * 40,
            timestamp=str(int(datetime.now(UTC).timestamp())),
            nonce="n",
        )

    assert captured.value.status_code == 413
