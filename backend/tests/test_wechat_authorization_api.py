"""WeChat Open Platform authorization and encrypted callback API tests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.api.platform_integrations as platform_api
from app.config import settings
from app.core.credential_crypto import decrypt_credential, encrypt_credential
from app.db import Base, get_session
from app.main import app
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
    session.add(WechatComponentCredential(platform_integration_id=integration.id))
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
    encryptor = Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_KEY[:16])).encryptor()
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


@pytest_asyncio.fixture
async def independent_wechat_database(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    database_path = tmp_path / "wechat-ordering.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def independent_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as request_session:
            yield request_session

    previous_override = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = independent_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as independent_client:
            yield independent_client, sessions
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous_override
        await engine.dispose()


async def _seed_independent_authorization_state(
    sessions: async_sessionmaker[AsyncSession],
    *,
    raw_state: str,
    issued_at: datetime,
) -> tuple[int, int]:
    async with sessions() as seed_session:
        org = Org(name=f"Independent WeChat org {raw_state[-6:]}")
        seed_session.add(org)
        await seed_session.flush()
        integration = await _seed_integration(seed_session, org_id=org.id)
        seed_session.add(
            Event(
                type="wechat.authorization.session.created",
                org_id=org.id,
                idempotency_key=platform_api._wechat_hash(raw_state),
                payload={
                    "state_id": platform_api._wechat_hash(f"id:{raw_state}"),
                    "org_id": org.id,
                    "initiated_by_id": 1,
                    "client_id": None,
                    "project_id": None,
                    "knowledge_base_id": None,
                    "issued_at": issued_at.isoformat(),
                    "expires_at": (issued_at + timedelta(minutes=10)).isoformat(),
                },
            )
        )
        await seed_session.commit()
        return org.id, integration.id


@pytest.mark.asyncio
async def test_create_authorization_session_returns_official_url_and_hashes_state(
    client, session, admin, monkeypatch
):
    result = await _create_authorization_session(client, session, admin, monkeypatch)

    assert result["state_id"]
    assert result["expires_at"]
    assert "pre_auth_code=pre-auth-code" in result["authorization_url"]
    assert "component_access_token" not in result["authorization_url"]
    assert parse_qs(urlparse(result["authorization_url"]).query)["auth_type"] == ["1"]
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
    result = await _create_authorization_session(client, session, admin, monkeypatch)
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
async def test_older_unauthorized_event_cannot_clear_newer_authorization(
    client, session, admin, monkeypatch
):
    _configure_callback_secrets(monkeypatch)
    result = await _create_authorization_session(client, session, admin, monkeypatch)
    state = _state_from_authorization_url(result["authorization_url"])
    created = await session.scalar(
        select(Event).where(Event.type == "wechat.authorization.session.created")
    )
    assert created is not None
    issued_at = datetime.fromisoformat(str(created.payload["issued_at"]))

    async def exchange(*_args, **_kwargs):
        return WechatAuthorizationGrant(
            authorizer_appid="wx_authorizer_ordered",
            authorizer_access_token="new-access",
            authorizer_refresh_token="new-refresh",
            expires_in=7200,
            func_info=[1],
        )

    monkeypatch.setattr(
        WechatOpenPlatformClient,
        "exchange_authorization_code",
        exchange,
    )
    callback = await client.get(
        "/platform-integrations/wechat/oauth/callback",
        params={"state": state, "auth_code": "new-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 307

    old_create_time = int(issued_at.timestamp()) - 10
    body, params = _encrypted_event(
        "<xml><AppId><![CDATA[wx_component_appid]]></AppId>"
        f"<CreateTime>{old_create_time}</CreateTime>"
        "<InfoType><![CDATA[unauthorized]]></InfoType>"
        "<AuthorizerAppid><![CDATA[wx_authorizer_ordered]]>"
        "</AuthorizerAppid></xml>"
    )
    assert (
        await client.post("/platform-integrations/wechat/events", params=params, content=body)
    ).status_code == 200

    auth = await session.scalar(
        select(PlatformAccountAuth).where(
            PlatformAccountAuth.external_open_id == "wx_authorizer_ordered"
        )
    )
    assert auth is not None
    assert auth.auth_status == "authorized"
    assert decrypt_credential(auth.access_token_encrypted or "") == "new-access"
    assert decrypt_credential(auth.refresh_token_encrypted or "") == "new-refresh"


@pytest.mark.asyncio
async def test_revocation_committed_during_code_exchange_wins_across_sessions(
    independent_wechat_database, monkeypatch
):
    _configure_callback_secrets(monkeypatch)
    independent_client, sessions = independent_wechat_database
    raw_state = "independent-revocation-race-state-value"
    issued_at = datetime.now(UTC) - timedelta(seconds=10)
    org_id, _integration_id = await _seed_independent_authorization_state(
        sessions,
        raw_state=raw_state,
        issued_at=issued_at,
    )
    exchange_started = asyncio.Event()
    release_exchange = asyncio.Event()

    async def exchange(*_args, **_kwargs):
        exchange_started.set()
        await release_exchange.wait()
        return WechatAuthorizationGrant(
            authorizer_appid="wx_authorizer_race",
            authorizer_access_token="stale-race-access",
            authorizer_refresh_token="stale-race-refresh",
            expires_in=7200,
            func_info=[1],
        )

    monkeypatch.setattr(
        WechatOpenPlatformClient,
        "exchange_authorization_code",
        exchange,
    )
    callback_task = asyncio.create_task(
        independent_client.get(
            "/platform-integrations/wechat/oauth/callback",
            params={"state": raw_state, "auth_code": "race-code"},
            follow_redirects=False,
        )
    )
    await exchange_started.wait()

    revocation_time = int(issued_at.timestamp()) + 5
    body, params = _encrypted_event(
        "<xml><AppId><![CDATA[wx_component_appid]]></AppId>"
        f"<CreateTime>{revocation_time}</CreateTime>"
        "<InfoType><![CDATA[unauthorized]]></InfoType>"
        "<AuthorizerAppid><![CDATA[wx_authorizer_race]]>"
        "</AuthorizerAppid></xml>",
        nonce="revocation-race",
    )
    revocation = await independent_client.post(
        "/platform-integrations/wechat/events", params=params, content=body
    )
    release_exchange.set()
    callback = await callback_task

    assert revocation.status_code == 200
    assert callback.status_code == 409
    async with sessions() as verification_session:
        assert (
            await verification_session.scalar(
                select(func.count(PlatformAccountAuth.id)).where(
                    PlatformAccountAuth.org_id == org_id,
                    PlatformAccountAuth.external_open_id == "wx_authorizer_race",
                )
            )
            == 0
        )
        assert (
            await verification_session.scalar(
                select(func.count(Event.id)).where(Event.type == "wechat.unauthorized")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_delayed_older_revocation_cannot_clear_new_grant_across_sessions(
    independent_wechat_database, monkeypatch
):
    _configure_callback_secrets(monkeypatch)
    independent_client, sessions = independent_wechat_database
    raw_state = "independent-new-grant-state-value"
    issued_at = datetime.now(UTC) - timedelta(seconds=10)
    org_id, _integration_id = await _seed_independent_authorization_state(
        sessions,
        raw_state=raw_state,
        issued_at=issued_at,
    )

    async def exchange(*_args, **_kwargs):
        return WechatAuthorizationGrant(
            authorizer_appid="wx_authorizer_newer",
            authorizer_access_token="newer-access",
            authorizer_refresh_token="newer-refresh",
            expires_in=7200,
            func_info=[1],
        )

    monkeypatch.setattr(
        WechatOpenPlatformClient,
        "exchange_authorization_code",
        exchange,
    )
    callback = await independent_client.get(
        "/platform-integrations/wechat/oauth/callback",
        params={"state": raw_state, "auth_code": "newer-code"},
        follow_redirects=False,
    )
    old_body, old_params = _encrypted_event(
        "<xml><AppId><![CDATA[wx_component_appid]]></AppId>"
        f"<CreateTime>{int(issued_at.timestamp()) - 5}</CreateTime>"
        "<InfoType><![CDATA[unauthorized]]></InfoType>"
        "<AuthorizerAppid><![CDATA[wx_authorizer_newer]]>"
        "</AuthorizerAppid></xml>",
        nonce="delayed-old-revocation",
    )
    delayed_revocation = await independent_client.post(
        "/platform-integrations/wechat/events",
        params=old_params,
        content=old_body,
    )

    assert callback.status_code == 307
    assert delayed_revocation.status_code == 200
    async with sessions() as verification_session:
        auth = await verification_session.scalar(
            select(PlatformAccountAuth).where(
                PlatformAccountAuth.org_id == org_id,
                PlatformAccountAuth.external_open_id == "wx_authorizer_newer",
            )
        )
        assert auth is not None
        assert auth.auth_status == "authorized"
        assert decrypt_credential(auth.access_token_encrypted or "") == "newer-access"
        assert decrypt_credential(auth.refresh_token_encrypted or "") == "newer-refresh"


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

    first = await client.post("/platform-integrations/wechat/events", params=params, content=body)
    second = await client.post("/platform-integrations/wechat/events", params=params, content=body)

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
        await session.scalars(select(Event).where(Event.type == "wechat.component_verify_ticket"))
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
                WechatComponentCredential.platform_integration_id.in_([first.id, second.id])
            )
        )
    ).all()
    assert len(credentials) == 2
    assert {
        decrypt_credential(item.component_verify_ticket_encrypted or "") for item in credentials
    } == {"shared-component-ticket"}


@pytest.mark.asyncio
async def test_older_component_ticket_cannot_overwrite_newer_ticket_for_any_org(
    client, session, admin, monkeypatch
):
    _configure_callback_secrets(monkeypatch)
    first = await _seed_integration(session, org_id=admin.org_id)
    second_org = Org(name="Second ordered-ticket tenant")
    session.add(second_org)
    await session.commit()
    second = await _seed_integration(session, org_id=second_org.id)
    newer_body, newer_params = _encrypted_event(
        "<xml><AppId><![CDATA[wx_component_appid]]></AppId>"
        "<CreateTime>1786410100</CreateTime>"
        "<InfoType><![CDATA[component_verify_ticket]]></InfoType>"
        "<ComponentVerifyTicket><![CDATA[newer-ticket]]>"
        "</ComponentVerifyTicket></xml>"
    )
    older_body, older_params = _encrypted_event(
        "<xml><AppId><![CDATA[wx_component_appid]]></AppId>"
        "<CreateTime>1786410000</CreateTime>"
        "<InfoType><![CDATA[component_verify_ticket]]></InfoType>"
        "<ComponentVerifyTicket><![CDATA[older-ticket]]>"
        "</ComponentVerifyTicket></xml>"
    )

    assert (
        await client.post(
            "/platform-integrations/wechat/events",
            params=newer_params,
            content=newer_body,
        )
    ).status_code == 200
    assert (
        await client.post(
            "/platform-integrations/wechat/events",
            params=older_params,
            content=older_body,
        )
    ).status_code == 200

    credentials = (
        await session.scalars(
            select(WechatComponentCredential).where(
                WechatComponentCredential.platform_integration_id.in_([first.id, second.id])
            )
        )
    ).all()
    assert {
        decrypt_credential(item.component_verify_ticket_encrypted or "") for item in credentials
    } == {"newer-ticket"}
    assert {
        int((item.ticket_received_at or datetime.min).replace(tzinfo=UTC).timestamp())
        for item in credentials
    } == {1786410100}


@pytest.mark.asyncio
async def test_concurrent_component_tickets_keep_newest_across_sessions(
    independent_wechat_database, monkeypatch
):
    _configure_callback_secrets(monkeypatch)
    independent_client, sessions = independent_wechat_database
    async with sessions() as seed_session:
        first_org = Org(name="Concurrent ticket org one")
        second_org = Org(name="Concurrent ticket org two")
        seed_session.add_all((first_org, second_org))
        await seed_session.commit()
        first = await _seed_integration(seed_session, org_id=first_org.id)
        second = await _seed_integration(seed_session, org_id=second_org.id)
        integration_ids = [first.id, second.id]

    newer_body, newer_params = _encrypted_event(
        "<xml><AppId><![CDATA[wx_component_appid]]></AppId>"
        "<CreateTime>1786410100</CreateTime>"
        "<InfoType><![CDATA[component_verify_ticket]]></InfoType>"
        "<ComponentVerifyTicket><![CDATA[concurrent-newer-ticket]]>"
        "</ComponentVerifyTicket></xml>",
        nonce="concurrent-newer",
    )
    older_body, older_params = _encrypted_event(
        "<xml><AppId><![CDATA[wx_component_appid]]></AppId>"
        "<CreateTime>1786410000</CreateTime>"
        "<InfoType><![CDATA[component_verify_ticket]]></InfoType>"
        "<ComponentVerifyTicket><![CDATA[concurrent-older-ticket]]>"
        "</ComponentVerifyTicket></xml>",
        nonce="concurrent-older",
    )
    newer_response, older_response = await asyncio.gather(
        independent_client.post(
            "/platform-integrations/wechat/events",
            params=newer_params,
            content=newer_body,
        ),
        independent_client.post(
            "/platform-integrations/wechat/events",
            params=older_params,
            content=older_body,
        ),
    )

    assert newer_response.status_code == 200
    assert older_response.status_code == 200
    async with sessions() as verification_session:
        credentials = (
            await verification_session.scalars(
                select(WechatComponentCredential).where(
                    WechatComponentCredential.platform_integration_id.in_(integration_ids)
                )
            )
        ).all()
        assert {
            decrypt_credential(item.component_verify_ticket_encrypted or "") for item in credentials
        } == {"concurrent-newer-ticket"}
        assert {
            int((item.ticket_received_at or datetime.min).replace(tzinfo=UTC).timestamp())
            for item in credentials
        } == {1786410100}


@pytest.mark.asyncio
async def test_event_rejects_stale_timestamp_and_wrong_appid(client, session, admin, monkeypatch):
    _configure_callback_secrets(monkeypatch)
    await _seed_integration(session, org_id=admin.org_id)
    inner = (
        "<xml><InfoType><![CDATA[component_verify_ticket]]></InfoType>"
        "<ComponentVerifyTicket><![CDATA[secret-ticket]]>"
        "</ComponentVerifyTicket></xml>"
    )
    stale_body, stale_params = _encrypted_event(inner, timestamp=1)
    wrong_body, wrong_params = _encrypted_event(inner, component_appid="wx_wrong_component")

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
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(Event.type == "wechat.component_verify_ticket")
        )
        == 0
    )


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

    first = await client.post("/platform-integrations/wechat/events", params=params, content=body)
    second = await client.post("/platform-integrations/wechat/events", params=params, content=body)

    assert first.status_code == 200
    assert second.status_code == 200
    events = (await session.scalars(select(Event).where(Event.type == f"wechat.{info_type}"))).all()
    assert len(events) == 1
    assert "temporary-secret-code" not in str(events[0].payload)
    assert (
        await session.scalar(
            select(func.count(PlatformAccountAuth.id)).where(
                PlatformAccountAuth.external_open_id == "wx_authorizer_pending"
            )
        )
        == 0
    )


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
        token_secret_ref="env:WECHAT_ACCESS_TOKEN",
        refresh_secret_ref="env:WECHAT_REFRESH_TOKEN",
    )
    douyin_auth = PlatformAccountAuth(
        org_id=admin.org_id,
        account_id=douyin_account.id,
        platform=Platform.DOUYIN.value,
        external_open_id="douyin-open-id",
        auth_status="authorized",
        access_token_encrypted=encrypt_credential("douyin-access"),
        refresh_token_encrypted=encrypt_credential("douyin-refresh"),
        token_secret_ref="env:DOUYIN_ACCESS_TOKEN",
        refresh_secret_ref="env:DOUYIN_REFRESH_TOKEN",
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

    first = await client.post("/platform-integrations/wechat/events", params=params, content=body)
    second = await client.post("/platform-integrations/wechat/events", params=params, content=body)

    assert first.status_code == 200
    assert second.status_code == 200
    await session.refresh(wechat_auth)
    await session.refresh(douyin_auth)
    assert wechat_auth.auth_status == "unauthorized"
    assert wechat_auth.access_token_encrypted is None
    assert wechat_auth.refresh_token_encrypted is None
    assert wechat_auth.token_secret_ref is None
    assert wechat_auth.refresh_secret_ref is None
    assert douyin_auth.auth_status == "authorized"
    assert decrypt_credential(douyin_auth.access_token_encrypted or "") == "douyin-access"
    assert decrypt_credential(douyin_auth.refresh_token_encrypted or "") == "douyin-refresh"
    assert douyin_auth.token_secret_ref == "env:DOUYIN_ACCESS_TOKEN"
    assert douyin_auth.refresh_secret_ref == "env:DOUYIN_REFRESH_TOKEN"
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(Event.type == "wechat.unauthorized")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_callback_config_is_required_and_event_body_is_bounded(client, monkeypatch):
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
async def test_wechat_xml_parser_rejects_utf16_dtd_entities_and_namespaces(client, monkeypatch):
    _configure_callback_secrets(monkeypatch)
    utf16_entity = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE xml [<!ENTITY expanded "entity-expanded">]>'
        "<xml><AppId>wx_component_appid</AppId>"
        "<Encrypt>&expanded;</Encrypt></xml>"
    ).encode("utf-16")
    utf8_entity = (
        b'<!DOCTYPE xml [<!ENTITY expanded "entity-expanded">]>'
        b"<xml><AppId>wx_component_appid</AppId>"
        b"<Encrypt>&expanded;</Encrypt></xml>"
    )
    namespaced = (
        b'<xml xmlns="urn:unexpected"><AppId>wx_component_appid</AppId>'
        b"<Encrypt>ciphertext</Encrypt></xml>"
    )
    prefixed_namespace = (
        b'<xml xmlns:unexpected="urn:unexpected"><AppId>wx_component_appid</AppId>'
        b"<Encrypt>ciphertext</Encrypt></xml>"
    )

    for payload in (utf16_entity, utf8_entity, namespaced, prefixed_namespace):
        with pytest.raises(HTTPException) as captured:
            platform_api._parse_wechat_xml(payload)
        assert captured.value.status_code == 400

    response = await client.post(
        "/platform-integrations/wechat/events",
        params={
            "msg_signature": "a" * 40,
            "timestamp": str(int(datetime.now(UTC).timestamp())),
            "nonce": "n",
        },
        content=utf16_entity,
    )
    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_status", ["authorized", "unauthorized"])
async def test_generic_patch_cannot_mutate_wechat_lifecycle_status(
    client, session, admin, requested_status
):
    integration = await _seed_integration(session, org_id=admin.org_id)
    response = await client.patch(
        "/platform-integrations/wechat_official_account",
        headers=_auth(await _token(client)),
        json={"auth_status": requested_status},
    )

    assert response.status_code == 422
    await session.refresh(integration)
    assert integration.auth_status == "unauthorized"


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
