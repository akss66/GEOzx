"""Token lifecycle tests for WeChat Open Platform third-party authorization."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.services.wechat_component as wechat_component_module
from app.config import settings
from app.core.credential_crypto import decrypt_credential, encrypt_credential
from app.models import (
    Account,
    Org,
    PlatformAccountAuth,
    PlatformIntegration,
    WechatComponentCredential,
)
from app.models.enums import Platform
from app.services.wechat_component import WechatIntegrationError, WechatOpenPlatformClient


@pytest.fixture(autouse=True)
def credential_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "credential_encryption_key",
        Fernet.generate_key().decode("ascii"),
    )


async def _seed_component(
    session,
    *,
    token: str | None = "old-component-token",
    expires_at: datetime | None = None,
) -> tuple[PlatformIntegration, WechatComponentCredential]:
    org = Org(name="WeChat component test org")
    integration = PlatformIntegration(
        org=org,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT.value,
        status="configured",
        client_key="component-appid",
        client_secret_ref="component-appsecret",
    )
    session.add(integration)
    await session.flush()
    credential = WechatComponentCredential(
        platform_integration_id=integration.id,
        component_verify_ticket_encrypted=encrypt_credential("component-ticket"),
        ticket_received_at=datetime.now(UTC),
        component_access_token_encrypted=encrypt_credential(token) if token else None,
        token_expires_at=expires_at,
    )
    session.add(credential)
    await session.commit()
    return integration, credential


async def _seed_authorizer(session) -> tuple[
    PlatformIntegration,
    WechatComponentCredential,
    Account,
    PlatformAccountAuth,
]:
    integration, component = await _seed_component(
        session,
        token="cached-component-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    account = Account(
        org_id=integration.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Authorized account",
    )
    auth = PlatformAccountAuth(
        org_id=integration.org_id,
        account=account,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT.value,
        external_open_id="authorizer-appid",
        auth_status="authorized",
        access_token_encrypted=encrypt_credential("authorizer-token-1"),
        refresh_token_encrypted=encrypt_credential("refresh-token-1"),
        token_expires_at=datetime.now(UTC) + timedelta(minutes=4),
    )
    session.add(auth)
    await session.commit()
    return integration, component, account, auth


class _SharedTestCoordinator:
    """Deterministic stand-in for a database-visible cross-process lock."""

    def __init__(self, locks: dict[tuple[str, int], asyncio.Lock]) -> None:
        self._locks = locks

    @asynccontextmanager
    async def transaction(self, token_session, kind: str, identifier: int):
        lock = self._locks.setdefault((kind, identifier), asyncio.Lock())
        async with lock:
            async with token_session.begin():
                yield


def _session_maker(session):
    assert session.bind is not None
    return async_sessionmaker(session.bind, expire_on_commit=False)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def test_component_token_refreshes_five_minutes_before_expiry(session) -> None:
    integration, credential = await _seed_component(
        session,
        expires_at=datetime.now(UTC) + timedelta(minutes=4, seconds=59),
    )
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert json.loads(request.content) == {
            "component_appid": "component-appid",
            "component_appsecret": "component-appsecret",
            "component_verify_ticket": "component-ticket",
        }
        return httpx.Response(
            200,
            json={"component_access_token": "component-token", "expires_in": 7200},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        service = WechatOpenPlatformClient(client=http_client)
        token = await service.get_component_access_token(session, integration.id)

    await session.refresh(credential)
    assert token == "component-token"
    assert calls == ["/cgi-bin/component/api_component_token"]
    assert credential.component_access_token_encrypted != "component-token"
    assert decrypt_credential(credential.component_access_token_encrypted or "") == token
    assert credential.token_expires_at is not None
    persisted_expiry = credential.token_expires_at
    if persisted_expiry.tzinfo is None:
        persisted_expiry = persisted_expiry.replace(tzinfo=UTC)
    assert persisted_expiry > datetime.now(UTC) + timedelta(hours=1)


async def test_component_token_uses_cache_outside_refresh_window(session) -> None:
    integration, _credential = await _seed_component(
        session,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=6),
    )

    async def reject_request(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("fresh component token must not make a remote request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_request)) as http_client:
        token = await WechatOpenPlatformClient(client=http_client).get_component_access_token(
            session, integration.id
        )

    assert token == "old-component-token"


async def test_concurrent_component_callers_share_one_refresh(session) -> None:
    integration, _credential = await _seed_component(
        session,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    request_count = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            json={"component_access_token": "component-token", "expires_in": 7200},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        service = WechatOpenPlatformClient(client=http_client)
        tokens = await asyncio.gather(
            service.get_component_access_token(session, integration.id),
            service.get_component_access_token(session, integration.id),
        )

    assert tokens == ["component-token", "component-token"]
    assert request_count == 1


async def test_authorizer_refresh_token_is_rotated_and_encrypted(session) -> None:
    _integration, _component, account, auth = await _seed_authorizer(session)
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.url.params["component_access_token"] == "cached-component-token"
        assert json.loads(request.content) == {
            "component_appid": "component-appid",
            "authorizer_appid": "authorizer-appid",
            "authorizer_refresh_token": "refresh-token-1",
        }
        return httpx.Response(
            200,
            json={
                "authorizer_access_token": "authorizer-token-2",
                "expires_in": 7200,
                "authorizer_refresh_token": "refresh-token-2",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        token = await WechatOpenPlatformClient(client=http_client).get_authorizer_access_token(
            session, account.id
        )

    await session.refresh(auth)
    assert token == "authorizer-token-2"
    assert calls == ["/cgi-bin/component/api_authorizer_token"]
    assert auth.access_token_encrypted != "authorizer-token-2"
    assert decrypt_credential(auth.access_token_encrypted or "") == "authorizer-token-2"
    assert auth.refresh_token_encrypted != "refresh-token-2"
    assert decrypt_credential(auth.refresh_token_encrypted or "") == "refresh-token-2"


async def test_authorizer_refresh_preserves_refresh_token_when_not_rotated(session) -> None:
    _integration, _component, account, auth = await _seed_authorizer(session)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"authorizer_access_token": "authorizer-token-2", "expires_in": 7200},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        await WechatOpenPlatformClient(client=http_client).get_authorizer_access_token(
            session, account.id
        )

    await session.refresh(auth)
    assert decrypt_credential(auth.refresh_token_encrypted or "") == "refresh-token-1"


async def test_exchange_authorization_code_normalizes_func_info(session) -> None:
    integration, _credential = await _seed_component(
        session,
        token="cached-component-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cgi-bin/component/api_query_auth"
        assert request.url.params["component_access_token"] == "cached-component-token"
        assert json.loads(request.content) == {
            "component_appid": "component-appid",
            "authorization_code": "authorization-code",
        }
        return httpx.Response(
            200,
            json={
                "authorization_info": {
                    "authorizer_appid": "authorizer-appid",
                    "authorizer_access_token": "authorizer-token",
                    "authorizer_refresh_token": "authorizer-refresh-token",
                    "expires_in": 7200,
                    "func_info": [
                        {"funcscope_category": {"id": 1}},
                        {"funcscope_category": {"id": 15}},
                    ],
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        grant = await WechatOpenPlatformClient(client=http_client).exchange_authorization_code(
            session, integration.id, "authorization-code"
        )

    assert grant.authorizer_appid == "authorizer-appid"
    assert grant.authorizer_access_token == "authorizer-token"
    assert grant.authorizer_refresh_token == "authorizer-refresh-token"
    assert grant.expires_in == 7200
    assert grant.func_info == [1, 15]


async def test_nonzero_wechat_error_is_typed_and_retryable(session) -> None:
    integration, credential = await _seed_component(
        session,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errcode": -1, "errmsg": "system busy", "rid": "request-rid"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatOpenPlatformClient(client=http_client).get_component_access_token(
                session, integration.id
            )

    await session.refresh(credential)
    error = captured.value
    assert error.code == -1
    assert error.retryable is True
    assert error.rid == "request-rid"
    assert error.endpoint == "/cgi-bin/component/api_component_token"
    assert credential.last_error == str(error)
    assert decrypt_credential(credential.component_access_token_encrypted or "") == (
        "old-component-token"
    )


async def test_malformed_wechat_response_is_rejected_before_persistence(session) -> None:
    integration, credential = await _seed_component(
        session,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"component_access_token": "new-token"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatOpenPlatformClient(client=http_client).get_component_access_token(
                session, integration.id
            )

    assert captured.value.code == "invalid_response"
    assert captured.value.retryable is False
    assert captured.value.endpoint == "/cgi-bin/component/api_component_token"
    assert decrypt_credential(credential.component_access_token_encrypted or "") == (
        "old-component-token"
    )


async def test_wechat_service_does_not_mutate_douyin_auth(session) -> None:
    org = Org(name="Douyin isolation org")
    account = Account(org=org, platform=Platform.DOUYIN, nickname="Douyin account")
    auth = PlatformAccountAuth(
        org=org,
        account=account,
        platform=Platform.DOUYIN.value,
        external_open_id="douyin-open-id",
        auth_status="authorized",
        access_token_encrypted=encrypt_credential("douyin-token"),
        refresh_token_encrypted=encrypt_credential("douyin-refresh-token"),
        token_expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    session.add(auth)
    await session.commit()

    async def reject_request(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Douyin auth must never reach the WeChat API")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_request)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatOpenPlatformClient(client=http_client).get_authorizer_access_token(
                session, account.id
            )

    assert captured.value.code == "authorizer_not_configured"
    assert decrypt_credential(auth.access_token_encrypted or "") == "douyin-token"
    assert decrypt_credential(auth.refresh_token_encrypted or "") == "douyin-refresh-token"


async def test_independent_component_clients_share_cross_process_coordination(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration, _credential = await _seed_component(
        session,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    monkeypatch.setattr(
        wechat_component_module,
        "_lock_for",
        lambda _kind, _identifier: asyncio.Lock(),
        raising=False,
    )
    request_count = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            json={"component_access_token": "component-token", "expires_in": 7200},
        )

    shared_locks: dict[tuple[str, int], asyncio.Lock] = {}
    maker = _session_maker(session)
    transport = httpx.MockTransport(handler)
    async with (
        maker() as session_a,
        maker() as session_b,
        httpx.AsyncClient(transport=transport) as http_a,
        httpx.AsyncClient(transport=transport) as http_b,
    ):
        service_a = WechatOpenPlatformClient(client=http_a)
        service_b = WechatOpenPlatformClient(client=http_b)
        service_a._coordinator = _SharedTestCoordinator(shared_locks)
        service_b._coordinator = _SharedTestCoordinator(shared_locks)
        tokens = await asyncio.gather(
            service_a.get_component_access_token(session_a, integration.id),
            service_b.get_component_access_token(session_b, integration.id),
        )

    assert tokens == ["component-token", "component-token"]
    assert request_count == 1


async def test_independent_authorizer_clients_share_cross_process_coordination(
    session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _integration, _component, account, _auth = await _seed_authorizer(session)
    monkeypatch.setattr(
        wechat_component_module,
        "_lock_for",
        lambda _kind, _identifier: asyncio.Lock(),
        raising=False,
    )
    request_count = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            json={
                "authorizer_access_token": "authorizer-token-2",
                "expires_in": 7200,
                "authorizer_refresh_token": "refresh-token-2",
            },
        )

    shared_locks: dict[tuple[str, int], asyncio.Lock] = {}
    maker = _session_maker(session)
    transport = httpx.MockTransport(handler)
    async with (
        maker() as session_a,
        maker() as session_b,
        httpx.AsyncClient(transport=transport) as http_a,
        httpx.AsyncClient(transport=transport) as http_b,
    ):
        service_a = WechatOpenPlatformClient(client=http_a)
        service_b = WechatOpenPlatformClient(client=http_b)
        service_a._coordinator = _SharedTestCoordinator(shared_locks)
        service_b._coordinator = _SharedTestCoordinator(shared_locks)
        tokens = await asyncio.gather(
            service_a.get_authorizer_access_token(session_a, account.id),
            service_b.get_authorizer_access_token(session_b, account.id),
        )

    assert tokens == ["authorizer-token-2", "authorizer-token-2"]
    assert request_count == 1


async def test_stale_caller_session_returns_fresh_authorizer_token(session) -> None:
    _integration, _component, account, _auth = await _seed_authorizer(session)
    maker = _session_maker(session)
    session_a = maker()
    session_b = maker()
    try:
        stale_auth = await session_a.scalar(
            select(PlatformAccountAuth).where(PlatformAccountAuth.account_id == account.id)
        )
        assert stale_auth is not None
        assert decrypt_credential(stale_auth.access_token_encrypted or "") == (
            "authorizer-token-1"
        )

        async def refresh_handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "authorizer_access_token": "authorizer-token-2",
                    "expires_in": 7200,
                    "authorizer_refresh_token": "refresh-token-2",
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(refresh_handler)
        ) as refresh_http:
            first_token = await WechatOpenPlatformClient(
                client=refresh_http
            ).get_authorizer_access_token(session_b, account.id)

        stale_session_requests = 0

        async def stale_handler(_request: httpx.Request) -> httpx.Response:
            nonlocal stale_session_requests
            stale_session_requests += 1
            return httpx.Response(
                200,
                json={
                    "authorizer_access_token": "wrong-second-refresh",
                    "expires_in": 7200,
                    "authorizer_refresh_token": "wrong-second-rotation",
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(stale_handler)
        ) as stale_http:
            second_token = await WechatOpenPlatformClient(
                client=stale_http
            ).get_authorizer_access_token(session_a, account.id)

        assert first_token == "authorizer-token-2"
        assert second_token == "authorizer-token-2"
        assert stale_session_requests == 0
    finally:
        await session_a.close()
        await session_b.close()


@pytest.mark.parametrize("remote_error", [False, True], ids=["success", "failure"])
async def test_token_service_never_commits_unrelated_caller_state(
    session,
    remote_error: bool,
) -> None:
    integration, _credential = await _seed_component(
        session,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    unrelated = Org(name=f"unrelated-caller-state-{remote_error}")
    session.add(unrelated)

    async def handler(_request: httpx.Request) -> httpx.Response:
        if remote_error:
            return httpx.Response(200, json={"errcode": -1, "errmsg": "system busy"})
        return httpx.Response(
            200,
            json={"component_access_token": "component-token", "expires_in": 7200},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        service = WechatOpenPlatformClient(client=http_client)
        if remote_error:
            with pytest.raises(WechatIntegrationError):
                await service.get_component_access_token(session, integration.id)
        else:
            assert await service.get_component_access_token(session, integration.id) == (
                "component-token"
            )

    assert unrelated.id is None
    assert unrelated in session.new


async def test_newer_rotated_authorizer_token_wins_stale_write_race(session) -> None:
    _integration, _component, account, auth = await _seed_authorizer(session)
    maker = _session_maker(session)

    async def handler(_request: httpx.Request) -> httpx.Response:
        async with maker() as writer:
            current = await writer.scalar(
                select(PlatformAccountAuth).where(
                    PlatformAccountAuth.account_id == account.id
                )
            )
            assert current is not None
            current.access_token_encrypted = encrypt_credential("newer-authorizer-token")
            current.refresh_token_encrypted = encrypt_credential("newer-refresh-token")
            current.token_expires_at = datetime.now(UTC) + timedelta(hours=2)
            await writer.commit()
        return httpx.Response(
            200,
            json={
                "authorizer_access_token": "stale-authorizer-token",
                "expires_in": 7200,
                "authorizer_refresh_token": "stale-refresh-token",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        returned = await WechatOpenPlatformClient(client=http_client).get_authorizer_access_token(
            session, account.id
        )

    await session.refresh(auth)
    assert returned == "newer-authorizer-token"
    assert decrypt_credential(auth.access_token_encrypted or "") == "newer-authorizer-token"
    assert decrypt_credential(auth.refresh_token_encrypted or "") == "newer-refresh-token"


async def test_revoked_authorizer_is_not_resurrected_by_stale_refresh(session) -> None:
    _integration, _component, account, auth = await _seed_authorizer(session)
    maker = _session_maker(session)

    async def handler(_request: httpx.Request) -> httpx.Response:
        async with maker() as writer:
            current = await writer.scalar(
                select(PlatformAccountAuth).where(
                    PlatformAccountAuth.account_id == account.id
                )
            )
            assert current is not None
            current.auth_status = "unauthorized"
            current.access_token_encrypted = encrypt_credential("revoked-access-token")
            current.refresh_token_encrypted = encrypt_credential("revoked-refresh-token")
            current.token_expires_at = datetime.now(UTC) + timedelta(hours=2)
            await writer.commit()
        return httpx.Response(
            200,
            json={
                "authorizer_access_token": "stale-authorizer-token",
                "expires_in": 7200,
                "authorizer_refresh_token": "stale-refresh-token",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatOpenPlatformClient(client=http_client).get_authorizer_access_token(
                session, account.id
            )

    await session.refresh(auth)
    assert captured.value.code == "authorizer_not_authorized"
    assert auth.auth_status == "unauthorized"
    assert decrypt_credential(auth.access_token_encrypted or "") == "revoked-access-token"
    assert decrypt_credential(auth.refresh_token_encrypted or "") == "revoked-refresh-token"


async def test_preexisting_unauthorized_authorizer_rejects_fresh_cached_token(
    session,
) -> None:
    _integration, _component, account, auth = await _seed_authorizer(session)
    auth.auth_status = "unauthorized"
    auth.token_expires_at = datetime.now(UTC) + timedelta(hours=2)
    await session.commit()
    original_access = auth.access_token_encrypted
    original_refresh = auth.refresh_token_encrypted
    original_expiry = auth.token_expires_at

    async def reject_request(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("unauthorized authorizer must not call WeChat")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reject_request)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatOpenPlatformClient(client=http_client).get_authorizer_access_token(
                session, account.id
            )

    await session.refresh(auth)
    assert captured.value.code == "authorizer_not_authorized"
    assert captured.value.retryable is False
    assert captured.value.endpoint == "/cgi-bin/component/api_authorizer_token"
    assert auth.auth_status == "unauthorized"
    assert auth.access_token_encrypted == original_access
    assert auth.refresh_token_encrypted == original_refresh
    assert auth.token_expires_at is not None
    assert original_expiry is not None
    assert _as_utc(auth.token_expires_at) == _as_utc(original_expiry)


async def test_preexisting_unauthorized_authorizer_rejects_expired_token(
    session,
) -> None:
    _integration, _component, account, auth = await _seed_authorizer(session)
    auth.auth_status = "unauthorized"
    auth.token_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()
    original_access = auth.access_token_encrypted
    original_refresh = auth.refresh_token_encrypted
    original_expiry = auth.token_expires_at
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "authorizer_access_token": "must-not-be-used",
                "expires_in": 7200,
                "authorizer_refresh_token": "must-not-be-rotated",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatOpenPlatformClient(client=http_client).get_authorizer_access_token(
                session, account.id
            )

    await session.refresh(auth)
    assert captured.value.code == "authorizer_not_authorized"
    assert "/cgi-bin/component/api_authorizer_token" not in calls
    assert auth.auth_status == "unauthorized"
    assert auth.access_token_encrypted == original_access
    assert auth.refresh_token_encrypted == original_refresh
    assert auth.token_expires_at is not None
    assert original_expiry is not None
    assert _as_utc(auth.token_expires_at) == _as_utc(original_expiry)


async def test_revocation_before_locked_reread_blocks_authorizer_refresh(session) -> None:
    _integration, _component, account, auth = await _seed_authorizer(session)
    original_access = auth.access_token_encrypted
    original_refresh = auth.refresh_token_encrypted
    original_expiry = auth.token_expires_at
    maker = _session_maker(session)
    calls: list[str] = []

    class RevokingCoordinator:
        @asynccontextmanager
        async def transaction(self, token_session, kind: str, _identifier: int):
            if kind == "authorizer":
                async with maker() as writer:
                    current = await writer.scalar(
                        select(PlatformAccountAuth).where(
                            PlatformAccountAuth.account_id == account.id
                        )
                    )
                    assert current is not None
                    current.auth_status = "unauthorized"
                    await writer.commit()
            async with token_session.begin():
                yield

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "authorizer_access_token": "must-not-be-used",
                "expires_in": 7200,
                "authorizer_refresh_token": "must-not-be-rotated",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        service = WechatOpenPlatformClient(client=http_client)
        service._coordinator = RevokingCoordinator()
        with pytest.raises(WechatIntegrationError) as captured:
            await service.get_authorizer_access_token(session, account.id)

    await session.refresh(auth)
    assert captured.value.code == "authorizer_not_authorized"
    assert "/cgi-bin/component/api_authorizer_token" not in calls
    assert auth.auth_status == "unauthorized"
    assert auth.access_token_encrypted == original_access
    assert auth.refresh_token_encrypted == original_refresh
    assert auth.token_expires_at is not None
    assert original_expiry is not None
    assert _as_utc(auth.token_expires_at) == _as_utc(original_expiry)


async def test_nonzero_errcode_survives_malformed_optional_metadata(session) -> None:
    integration, _credential = await _seed_component(
        session,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"errcode": -1, "errmsg": {"unsafe": "value"}, "rid": ["bad-rid"]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(WechatIntegrationError) as captured:
            await WechatOpenPlatformClient(client=http_client).get_component_access_token(
                session, integration.id
            )

    assert captured.value.code == -1
    assert captured.value.retryable is True
    assert captured.value.endpoint == "/cgi-bin/component/api_component_token"
    assert captured.value.rid is None
