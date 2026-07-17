"""Douyin Open Platform helpers.

The URLs and parameters are based on Douyin Open Platform official docs for
web OAuth, JS SDK access, and JS SDK signature generation:
- https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/sdk/web-app/web/permission
- https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/sdk/web-app/js/js-access
- https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/sdk/web-app/js/signature
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urldefrag, urlencode

import httpx

from app.models.enums import MetricSource, Platform

DOUYIN_AUTHORIZE_URL = "https://open.douyin.com/platform/oauth/connect/"
DOUYIN_ACCESS_TOKEN_URL = "https://open.douyin.com/oauth/access_token/"
DOUYIN_REFRESH_TOKEN_URL = "https://open.douyin.com/oauth/refresh_token/"
DOUYIN_CLIENT_TOKEN_URL = "https://open.douyin.com/oauth/client_token/"
DOUYIN_JSB_TICKET_URL = "https://open.douyin.com/js/getticket/"
DOUYIN_USER_INFO_URL = "https://open.douyin.com/oauth/userinfo/"
DOUYIN_VIDEO_LIST_URL = "https://open.douyin.com/video/list"
DOUYIN_VIDEO_DATA_URL = "https://open.douyin.com/video/data"
DEFAULT_DOUYIN_SECRET_REF = "vault://dyflow/douyin/client-secret"

_ticket_cache: dict[tuple[int, str], CachedSecret] = {}
_client_token_cache: dict[tuple[int, str], CachedSecret] = {}


class DouyinIntegrationError(RuntimeError):
    """Base error for Douyin platform integration failures."""


class SecretNotConfiguredError(DouyinIntegrationError):
    """Raised when a configured secret reference cannot be resolved locally."""


@dataclass
class CachedSecret:
    value: str
    expires_at: datetime

    def is_valid(self) -> bool:
        return self.expires_at > datetime.now(UTC) + timedelta(seconds=60)


def build_douyin_authorization_url(
    *,
    client_key: str,
    redirect_uri: str,
    scopes: list[str],
    state: str,
) -> str:
    params = {
        "client_key": client_key,
        "response_type": "code",
        "scope": ",".join(scopes),
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{DOUYIN_AUTHORIZE_URL}?{urlencode(params)}"


def normalize_signature_url(raw_url: str) -> str:
    return urldefrag(raw_url)[0]


def create_js_signature(*, ticket: str, url: str) -> dict[str, Any]:
    nonce_str = secrets.token_hex(8)
    timestamp = int(time.time())
    normalized_url = normalize_signature_url(url)
    plain = (
        f"jsapi_ticket={ticket}&noncestr={nonce_str}"
        f"&timestamp={timestamp}&url={normalized_url}"
    )
    return {
        "nonce_str": nonce_str,
        "timestamp": timestamp,
        "url": normalized_url,
        "signature": hashlib.md5(plain.encode("utf-8")).hexdigest(),
    }


def resolve_secret_ref(secret_ref: str | None, *, platform: Platform) -> str:
    if not secret_ref:
        raise SecretNotConfiguredError("client secret reference is not configured")
    if secret_ref.startswith("env:"):
        env_name = secret_ref.removeprefix("env:").strip()
        value = os.environ.get(env_name) or _settings_secret(env_name)
        if value:
            return value
        raise SecretNotConfiguredError(f"environment secret {env_name} is not configured")
    if secret_ref.startswith("vault://"):
        candidates = _vault_env_candidates(secret_ref, platform=platform)
        for env_name in candidates:
            value = os.environ.get(env_name) or _settings_secret(env_name)
            if value:
                return value
        raise SecretNotConfiguredError(
            "vault secret is not available in this runtime; set DOUYIN_CLIENT_SECRET "
            "or DYFLOW_DOUYIN_CLIENT_SECRET"
        )
    return secret_ref


def resolve_douyin_account_token_ref(token_secret_ref: str | None) -> str:
    """Resolve an account access token reference without falling back to app secrets."""
    if not token_secret_ref:
        raise SecretNotConfiguredError("account access token is not configured")
    if token_secret_ref.startswith("env:"):
        env_name = token_secret_ref.removeprefix("env:").strip()
        value = os.environ.get(env_name) or _settings_secret(env_name)
        if value:
            return value
        raise SecretNotConfiguredError("account access token environment secret is not configured")
    if token_secret_ref.startswith("vault://"):
        normalized = (
            token_secret_ref.removeprefix("vault://")
            .replace("/", "_")
            .replace("-", "_")
            .replace(".", "_")
            .upper()
        )
        env_name = f"DYFLOW_VAULT_{normalized}"
        value = os.environ.get(env_name) or _settings_secret(env_name)
        if value:
            return value
        raise SecretNotConfiguredError("account access token vault secret is not available")
    return token_secret_ref


def _settings_secret(env_name: str) -> str | None:
    from app.config import settings

    setting_name = env_name.lower()
    value = getattr(settings, setting_name, None)
    return str(value) if value else None


def _vault_env_candidates(secret_ref: str, *, platform: Platform) -> list[str]:
    normalized = (
        secret_ref.removeprefix("vault://")
        .replace("/", "_")
        .replace("-", "_")
        .replace(".", "_")
        .upper()
    )
    platform_name = platform.value.upper()
    return [
        f"{platform_name}_CLIENT_SECRET",
        f"DYFLOW_{platform_name}_CLIENT_SECRET",
        f"DYFLOW_VAULT_{normalized}",
    ]


async def exchange_douyin_access_token(
    *, client_key: str, client_secret: str, code: str
) -> dict[str, Any]:
    return await _post_form_douyin_data(
        DOUYIN_ACCESS_TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        required_field="access_token",
    )


async def refresh_douyin_access_token(
    *,
    client_key: str,
    refresh_token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    data = {
        "client_key": client_key,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    return await _post_form_douyin_data(
        DOUYIN_REFRESH_TOKEN_URL,
        data=data,
        required_field="access_token",
        client=client,
    )


async def get_douyin_client_token(
    *, org_id: int, client_key: str, client_secret: str
) -> str:
    cache_key = (org_id, client_key)
    cached = _client_token_cache.get(cache_key)
    if cached and cached.is_valid():
        return cached.value

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            DOUYIN_CLIENT_TOKEN_URL,
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "client_credential",
            },
        )
    data = _extract_douyin_data(resp.json(), "access_token")
    token = str(data["access_token"])
    expires_in = int(data.get("expires_in") or 3600)
    _client_token_cache[cache_key] = CachedSecret(
        value=token,
        expires_at=datetime.now(UTC) + timedelta(seconds=max(expires_in - 60, 60)),
    )
    return token


async def get_douyin_jsb_ticket(*, integration, client_secret: str) -> str:
    if integration.client_key is None:
        raise DouyinIntegrationError("client_key is not configured")
    cache_key = (integration.org_id, integration.client_key)
    cached = _ticket_cache.get(cache_key)
    if cached and cached.is_valid():
        return cached.value

    client_token = await get_douyin_client_token(
        org_id=integration.org_id,
        client_key=integration.client_key,
        client_secret=client_secret,
    )
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            DOUYIN_JSB_TICKET_URL,
            params={"access_token": client_token},
        )
    data = _extract_douyin_data(resp.json(), "ticket")
    ticket = str(data["ticket"])
    expires_in = int(data.get("expires_in") or 3600)
    _ticket_cache[cache_key] = CachedSecret(
        value=ticket,
        expires_at=datetime.now(UTC) + timedelta(seconds=max(expires_in - 60, 60)),
    )
    return ticket


async def fetch_douyin_user_info(
    *,
    access_token: str,
    open_id: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch public user info from Douyin OpenAPI.

    This mirrors the official `/oauth/userinfo/` capability and keeps
    transport injectable so tests never hit the real platform.
    """
    return await _post_form_douyin_data(
        DOUYIN_USER_INFO_URL,
        data={"access_token": access_token, "open_id": open_id},
        required_field="open_id",
        client=client,
    )


async def fetch_douyin_video_list(
    *,
    access_token: str,
    open_id: str,
    cursor: int = 0,
    count: int = 20,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch published video list from Douyin OpenAPI."""
    return await _get_douyin_data(
        DOUYIN_VIDEO_LIST_URL,
        params={
            "access_token": access_token,
            "open_id": open_id,
            "cursor": cursor,
            "count": count,
        },
        required_field="list",
        client=client,
    )


async def fetch_douyin_video_data(
    *,
    access_token: str,
    open_id: str,
    item_ids: list[str],
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Fetch performance data for specific Douyin videos."""
    return await _post_douyin_data(
        DOUYIN_VIDEO_DATA_URL,
        params={"access_token": access_token, "open_id": open_id},
        json={"item_ids": item_ids},
        required_field="list",
        client=client,
    )


def normalize_douyin_user_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert Douyin user info into the platform account profile shape."""
    return {
        "external_open_id": payload.get("open_id"),
        "union_id": payload.get("union_id"),
        "nickname": payload.get("nickname"),
        "avatar": payload.get("avatar"),
        "raw_profile": payload,
    }


def normalize_douyin_video_metrics(
    items: list[dict[str, Any]],
    *,
    account_id: int,
    default_stat_date: date | None = None,
) -> list[dict[str, Any]]:
    """Map Douyin video statistics to the current review snapshot fields.

    Douyin's basic video list gives counts, not rates. We derive rates from
    `play_count` and keep unknown fields at safe zero defaults.
    """
    snapshots: list[dict[str, Any]] = []
    for item in items:
        statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        play = _as_int(statistics.get("play_count"))
        share = _as_int(statistics.get("share_count") or statistics.get("forward_count"))
        stat_date = _stat_date_from_timestamp(item.get("create_time"), default_stat_date)
        snapshots.append(
            {
                "account_id": account_id,
                "source": MetricSource.DOUYIN,
                "stat_date": stat_date,
                "title": item.get("title"),
                "play": play,
                "exposure": play,
                "completion_rate": 0.0,
                "like_rate": _rate(_as_int(statistics.get("digg_count")), play),
                "comment_rate": _rate(_as_int(statistics.get("comment_count")), play),
                "share_rate": _rate(share, play),
                "follower_delta": 0,
                "external_item_id": item.get("item_id"),
            }
        )
    return snapshots


async def _get_douyin_data(
    url: str,
    *,
    params: dict[str, Any],
    required_field: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    try:
        if client is not None:
            resp = await client.get(url, params=params)
            return _extract_response_data(resp, required_field)
        async with httpx.AsyncClient(timeout=10) as owned_client:
            resp = await owned_client.get(url, params=params)
            return _extract_response_data(resp, required_field)
    except httpx.RequestError as exc:
        raise DouyinIntegrationError(f"Douyin OpenAPI request failed: {exc}") from exc


async def _post_douyin_data(
    url: str,
    *,
    params: dict[str, Any],
    json: dict[str, Any],
    required_field: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    try:
        if client is not None:
            resp = await client.post(url, params=params, json=json)
            return _extract_response_data(resp, required_field)
        async with httpx.AsyncClient(timeout=10) as owned_client:
            resp = await owned_client.post(url, params=params, json=json)
            return _extract_response_data(resp, required_field)
    except httpx.RequestError as exc:
        raise DouyinIntegrationError(f"Douyin OpenAPI request failed: {exc}") from exc


async def _post_form_douyin_data(
    url: str,
    *,
    data: dict[str, Any],
    required_field: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    try:
        if client is not None:
            resp = await client.post(url, data=data)
            return _extract_response_data(resp, required_field)
        async with httpx.AsyncClient(timeout=10) as owned_client:
            resp = await owned_client.post(url, data=data)
            return _extract_response_data(resp, required_field)
    except httpx.RequestError as exc:
        raise DouyinIntegrationError(f"Douyin OpenAPI request failed: {exc}") from exc


def _extract_response_data(resp: httpx.Response, required_field: str) -> dict[str, Any]:
    if resp.status_code != 200:
        raise DouyinIntegrationError(f"Douyin OpenAPI request failed: status={resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise DouyinIntegrationError("Douyin OpenAPI returned invalid JSON") from exc
    return _extract_douyin_data(payload, required_field)


def _as_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _rate(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(part / total, 6)


def _stat_date_from_timestamp(value: object, fallback: date | None) -> date:
    if value:
        try:
            return datetime.fromtimestamp(int(value), tz=UTC).date()
        except (TypeError, ValueError, OSError):
            pass
    return fallback or datetime.now(UTC).date()


def _extract_douyin_data(payload: dict[str, Any], required_field: str) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload
    error_code = (
        data.get("error_code")
        or data.get("err_code")
        or data.get("errcode")
        or data.get("err_no")
        or data.get("code")
    )
    if error_code not in (None, 0, "0"):
        raise DouyinIntegrationError(str(data.get("description") or data.get("message") or data))
    if required_field not in data:
        raise DouyinIntegrationError(f"Douyin response missing {required_field}")
    return data
