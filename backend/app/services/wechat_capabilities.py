"""Fail-closed WeChat Official Account capability normalization.

Official API references:
- https://developers.weixin.qq.com/doc/oplatform/openApi/OpenApiDoc/authorization-management/getAuthorizerInfo.html
- https://developers.weixin.qq.com/doc/service/api/material/permanent/api_getmaterialcount
- https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_count
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlatformAccountAuth, PlatformIntegration
from app.models.enums import Platform
from app.schemas.platform import CapabilityState, WechatCapabilitySnapshot
from app.services.wechat_component import WECHAT_API_BASE_URL, WechatOpenPlatformClient

CONTENT_PERMISSION_IDS = frozenset({11, 100})
ANALYTICS_PERMISSION_IDS = frozenset({7})
AUTHORIZER_INFO_ENDPOINT = "/cgi-bin/component/api_get_authorizer_info"
MATERIAL_COUNT_ENDPOINT = "/cgi-bin/material/get_materialcount"
DRAFT_COUNT_ENDPOINT = "/cgi-bin/draft/count"


class WechatCapabilityError(RuntimeError):
    """Stable, secret-free capability-probe failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _qualification_reason(account_profile: dict[str, Any]) -> str | None:
    service_type = account_profile.get("service_type_info")
    if not isinstance(service_type, dict) or type(service_type.get("id")) is not int:
        return "account_qualification_unknown"
    if service_type["id"] not in {0, 1, 2}:
        return "unsupported_account_type"
    verify_type = account_profile.get("verify_type_info")
    if not isinstance(verify_type, dict) or type(verify_type.get("id")) is not int:
        return "account_qualification_unknown"
    if verify_type["id"] < 0:
        return "account_not_verified"
    return None


def _state(
    *,
    required_ids: frozenset[int],
    component_permission_ids: set[int],
    account_permission_ids: set[int],
    qualification_reason: str | None,
    probe_ok: bool,
) -> CapabilityState:
    permission_ids = sorted(required_ids)
    component_required = required_ids.intersection(component_permission_ids)
    if not component_required:
        return CapabilityState(
            can_use=False,
            reason="component_permission_missing",
            permission_ids=permission_ids,
        )
    if not component_required.intersection(account_permission_ids):
        return CapabilityState(
            can_use=False,
            reason="account_permission_missing",
            permission_ids=permission_ids,
        )
    if qualification_reason:
        return CapabilityState(
            can_use=False,
            reason=qualification_reason,
            permission_ids=permission_ids,
        )
    if not probe_ok:
        return CapabilityState(
            can_use=False,
            reason="live_probe_failed",
            permission_ids=permission_ids,
        )
    return CapabilityState(can_use=True, permission_ids=permission_ids)


def normalize_capabilities(
    *,
    func_info: list[int],
    account_profile: dict[str, Any],
    account_id: int = 0,
    component_permission_ids: set[int] | None = None,
    authorizer_info_probe_ok: bool = True,
    material_probe_ok: bool = True,
    draft_probe_ok: bool = True,
    checked_at: datetime | None = None,
) -> WechatCapabilitySnapshot:
    """Build a stable, fail-closed snapshot from permission and probe facts."""
    component_permissions = set(component_permission_ids or set())
    account_permissions = set(func_info)
    qualification_reason = _qualification_reason(account_profile)
    content_material = _state(
        required_ids=CONTENT_PERMISSION_IDS,
        component_permission_ids=component_permissions,
        account_permission_ids=account_permissions,
        qualification_reason=qualification_reason,
        probe_ok=authorizer_info_probe_ok and material_probe_ok,
    )
    content_draft = _state(
        required_ids=CONTENT_PERMISSION_IDS,
        component_permission_ids=component_permissions,
        account_permission_ids=account_permissions,
        qualification_reason=qualification_reason,
        probe_ok=authorizer_info_probe_ok and draft_probe_ok,
    )
    return WechatCapabilitySnapshot(
        account_id=account_id,
        upload_article_image=content_material,
        add_permanent_material=content_material,
        draft_add=content_draft,
        draft_get=content_draft,
        draft_update=content_draft,
        analytics=_state(
            required_ids=ANALYTICS_PERMISSION_IDS,
            component_permission_ids=component_permissions,
            account_permission_ids=account_permissions,
            qualification_reason=qualification_reason,
            probe_ok=authorizer_info_probe_ok,
        ),
        freepublish=CapabilityState(
            can_use=False,
            reason="disabled_by_product_policy",
            permission_ids=sorted(CONTENT_PERMISSION_IDS),
        ),
        checked_at=checked_at or datetime.now(UTC),
    )


def _numeric_scopes(values: list[str] | list[int] | None) -> set[int]:
    if not values:
        return set()
    result: set[int] = set()
    for value in values:
        if isinstance(value, int) and not isinstance(value, bool):
            result.add(value)
        elif isinstance(value, str) and value.isdigit():
            result.add(int(value))
    return result


def _func_info_ids(value: object) -> set[int]:
    if not isinstance(value, list):
        raise ValueError("invalid func_info")
    result: set[int] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("invalid func_info")
        category = item.get("funcscope_category")
        if not isinstance(category, dict) or type(category.get("id")) is not int:
            raise ValueError("invalid func_info")
        result.add(category["id"])
    return result


async def _request_wechat_json(
    endpoint: str,
    *,
    params: dict[str, str],
    json: dict[str, object] | None = None,
    method: Literal["GET", "POST"] = "POST",
) -> dict[str, Any]:
    """Issue a bounded WeChat request without exposing provider response data."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.request(
                method, f"{WECHAT_API_BASE_URL}{endpoint}", params=params, json=json
            )
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise WechatCapabilityError("live_probe_failed") from exc
    if response.is_error or not isinstance(payload, dict):
        raise WechatCapabilityError("live_probe_failed")
    errcode = payload.get("errcode")
    if errcode not in (None, 0):
        raise WechatCapabilityError("live_probe_failed")
    return payload


async def _probe_authorizer_info(
    *,
    component_access_token: str,
    component_appid: str,
    authorizer_appid: str,
) -> tuple[set[int], dict[str, Any]]:
    payload = await _request_wechat_json(
        AUTHORIZER_INFO_ENDPOINT,
        params={"component_access_token": component_access_token},
        json={
            "component_appid": component_appid,
            "authorizer_appid": authorizer_appid,
        },
    )
    profile = payload.get("authorizer_info")
    authorization = payload.get("authorization_info")
    if not isinstance(profile, dict) or not isinstance(authorization, dict):
        raise WechatCapabilityError("live_probe_failed")
    try:
        return _func_info_ids(authorization.get("func_info")), profile
    except ValueError as exc:
        raise WechatCapabilityError("live_probe_failed") from exc


async def _probe_count(
    endpoint: str,
    *,
    authorizer_access_token: str,
    expected_field: str,
    method: Literal["GET", "POST"],
) -> bool:
    try:
        payload = await _request_wechat_json(
            endpoint,
            params={"access_token": authorizer_access_token},
            json=None if method == "GET" else {},
            method=method,
        )
    except WechatCapabilityError:
        return False
    count = payload.get(expected_field)
    return type(count) is int and count >= 0


async def probe_wechat_capabilities(
    session: AsyncSession,
    account_id: int,
) -> WechatCapabilitySnapshot:
    """Probe an authorized account with only harmless, read-only endpoints."""
    auth = await session.scalar(
        select(PlatformAccountAuth).where(
            PlatformAccountAuth.account_id == account_id,
            PlatformAccountAuth.platform == Platform.WECHAT_OFFICIAL_ACCOUNT.value,
        )
    )
    if auth is None or auth.auth_status != "authorized" or not auth.external_open_id:
        raise WechatCapabilityError("account_not_authorized")
    integration = await session.scalar(
        select(PlatformIntegration).where(
            PlatformIntegration.org_id == auth.org_id,
            PlatformIntegration.platform == Platform.WECHAT_OFFICIAL_ACCOUNT.value,
        )
    )
    if integration is None or not integration.client_key:
        raise WechatCapabilityError("integration_not_configured")

    client = WechatOpenPlatformClient()
    local_permissions = _numeric_scopes(auth.scopes)
    profile = auth.raw_profile if isinstance(auth.raw_profile, dict) else {}
    authorizer_info_ok = True
    try:
        component_access_token = await client.get_component_access_token(session, integration.id)
        live_permissions, profile = await _probe_authorizer_info(
            component_access_token=component_access_token,
            component_appid=integration.client_key,
            authorizer_appid=auth.external_open_id,
        )
    except Exception:  # Provider details must not cross the capability boundary.
        authorizer_info_ok = False
        live_permissions = local_permissions

    try:
        authorizer_access_token = await client.get_authorizer_access_token(session, account_id)
    except Exception:  # Token/provider details must not cross the capability boundary.
        material_probe_ok = False
        draft_probe_ok = False
    else:
        material_probe_ok = await _probe_count(
            MATERIAL_COUNT_ENDPOINT,
            authorizer_access_token=authorizer_access_token,
            expected_field="image_count",
            method="GET",
        )
        draft_probe_ok = await _probe_count(
            DRAFT_COUNT_ENDPOINT,
            authorizer_access_token=authorizer_access_token,
            expected_field="total_count",
            method="POST",
        )

    return normalize_capabilities(
        account_id=account_id,
        func_info=sorted(live_permissions),
        account_profile=profile,
        component_permission_ids=_numeric_scopes(integration.scopes),
        authorizer_info_probe_ok=authorizer_info_ok,
        material_probe_ok=material_probe_ok,
        draft_probe_ok=draft_probe_ok,
    )
