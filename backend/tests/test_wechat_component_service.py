"""Token lifecycle tests for WeChat Open Platform third-party authorization."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet

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

    assert token == "component-token"
    assert calls == ["/cgi-bin/component/api_component_token"]
    assert credential.component_access_token_encrypted != "component-token"
    assert decrypt_credential(credential.component_access_token_encrypted or "") == token
    assert credential.token_expires_at is not None
    assert credential.token_expires_at > datetime.now(UTC) + timedelta(hours=1)


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
