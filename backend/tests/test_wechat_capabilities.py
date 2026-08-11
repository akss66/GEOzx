"""WeChat Official Account capability probing contracts."""

from datetime import UTC, datetime

import pytest

import app.api.platform_integrations as platform_api
from app.models import Account, Org, PlatformAccountAuth, PlatformIntegration
from app.models.enums import Platform
from app.schemas.platform import CapabilityState, WechatCapabilitySnapshot
from app.services.wechat_capabilities import normalize_capabilities


async def _token(client) -> str:
    response = await client.post(
        "/auth/login",
        json={"email": "admin@test.com", "password": "admin-pw-123"},
    )
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_capability_snapshot_never_enables_publish_in_v1() -> None:
    """Removing the product-policy gate must never make publish available."""
    snapshot = normalize_capabilities(
        func_info=[1, 7, 11, 100],
        account_profile={"service_type_info": {"id": 0}, "verify_type_info": {"id": 0}},
    )

    assert snapshot.draft_add.can_use is True
    assert snapshot.freepublish.can_use is False
    assert snapshot.freepublish.reason == "disabled_by_product_policy"


@pytest.mark.asyncio
async def test_probe_fails_closed_when_live_authorizer_permissions_revoke_content(
    session, admin, monkeypatch
) -> None:
    """Using stale local content grants after the live check must remain impossible."""
    integration = PlatformIntegration(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT.value,
        status="configured",
        client_key="component-appid",
        client_secret_ref="env:WECHAT_COMPONENT_APP_SECRET",
        scopes=["7", "11", "100"],
    )
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        external_account_id="authorizer-appid",
        nickname="WeChat account",
    )
    session.add_all((integration, account))
    await session.flush()
    session.add(
        PlatformAccountAuth(
            org_id=admin.org_id,
            account_id=account.id,
            platform=Platform.WECHAT_OFFICIAL_ACCOUNT.value,
            external_open_id="authorizer-appid",
            auth_status="authorized",
            scopes=["7", "11", "100"],
        )
    )
    await session.commit()

    from app.services import wechat_capabilities

    async def fake_authorizer_token(*_args, **_kwargs) -> str:
        return "authorizer-token"

    async def fake_component_token(*_args, **_kwargs) -> str:
        return "component-token"

    async def fake_wechat_request(endpoint: str, **_kwargs) -> dict:
        if endpoint == wechat_capabilities.AUTHORIZER_INFO_ENDPOINT:
            return {
                "authorizer_info": {
                    "service_type_info": {"id": 0},
                    "verify_type_info": {"id": 0},
                },
                "authorization_info": {
                    "func_info": [{"funcscope_category": {"id": 7}}]
                },
            }
        if endpoint == wechat_capabilities.MATERIAL_COUNT_ENDPOINT:
            assert _kwargs["method"] == "GET"
            assert _kwargs["json"] is None
            return {"image_count": 0}
        if endpoint == wechat_capabilities.DRAFT_COUNT_ENDPOINT:
            return {"total_count": 0}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(
        wechat_capabilities.WechatOpenPlatformClient,
        "get_authorizer_access_token",
        fake_authorizer_token,
    )
    monkeypatch.setattr(
        wechat_capabilities.WechatOpenPlatformClient,
        "get_component_access_token",
        fake_component_token,
    )
    monkeypatch.setattr(wechat_capabilities, "_request_wechat_json", fake_wechat_request)

    snapshot = await wechat_capabilities.probe_wechat_capabilities(session, account.id)

    assert snapshot.draft_add.can_use is False
    assert snapshot.draft_add.reason == "account_permission_missing"
    assert snapshot.analytics.can_use is True


@pytest.mark.asyncio
async def test_capability_endpoint_returns_403_for_an_inaccessible_account(
    client, session, admin
) -> None:
    """Dropping account-scope enforcement must not expose a cross-org snapshot."""
    other_org = Org(name="Other organization")
    account = Account(
        org=other_org,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Other WeChat account",
    )
    session.add(account)
    await session.commit()

    response = await client.get(
        f"/accounts/{account.id}/platform-capabilities",
        headers=_auth(await _token(client)),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_capability_endpoint_returns_409_when_wechat_is_not_authorized(
    client, session, admin
) -> None:
    """Removing authorization-state validation must not trigger a provider probe."""
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Unauthorized WeChat account",
    )
    session.add(account)
    await session.commit()

    response = await client.get(
        f"/accounts/{account.id}/platform-capabilities",
        headers=_auth(await _token(client)),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_capability_endpoint_returns_only_the_typed_snapshot(
    client, session, admin, monkeypatch
) -> None:
    """Returning probe internals must not leak provider tokens or errors to clients."""
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Authorized WeChat account",
    )
    session.add(account)
    await session.flush()
    session.add(
        PlatformAccountAuth(
            org_id=admin.org_id,
            account_id=account.id,
            platform=Platform.WECHAT_OFFICIAL_ACCOUNT.value,
            external_open_id="authorizer-appid",
            auth_status="authorized",
        )
    )
    await session.commit()
    unavailable = CapabilityState(
        can_use=False,
        reason="component_permission_missing",
        permission_ids=[11, 100],
    )

    async def fake_probe(*_args, **_kwargs) -> WechatCapabilitySnapshot:
        return WechatCapabilitySnapshot(
            account_id=account.id,
            upload_article_image=unavailable,
            add_permanent_material=unavailable,
            draft_add=unavailable,
            draft_get=unavailable,
            draft_update=unavailable,
            analytics=CapabilityState(
                can_use=False,
                reason="component_permission_missing",
                permission_ids=[7],
            ),
            freepublish=CapabilityState(
                can_use=False,
                reason="disabled_by_product_policy",
                permission_ids=[11, 100],
            ),
            checked_at=datetime.now(UTC),
        )

    monkeypatch.setattr(platform_api, "probe_wechat_capabilities", fake_probe)
    response = await client.get(
        f"/accounts/{account.id}/platform-capabilities",
        headers=_auth(await _token(client)),
    )

    assert response.status_code == 200
    assert response.json()["freepublish"]["reason"] == "disabled_by_product_policy"
    assert "authorizer-token" not in response.text
