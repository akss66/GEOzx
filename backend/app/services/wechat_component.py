"""Secure WeChat Open Platform component and authorizer token lifecycle."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar
from weakref import WeakValueDictionary

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)
from app.integrations.douyin import SecretNotConfiguredError, resolve_secret_ref
from app.models import PlatformAccountAuth, PlatformIntegration, WechatComponentCredential
from app.models.enums import Platform
from app.schemas.platform import WechatAuthorizationGrant

WECHAT_API_BASE_URL = "https://api.weixin.qq.com"
COMPONENT_TOKEN_ENDPOINT = "/cgi-bin/component/api_component_token"
AUTHORIZER_TOKEN_ENDPOINT = "/cgi-bin/component/api_authorizer_token"
QUERY_AUTH_ENDPOINT = "/cgi-bin/component/api_query_auth"
TOKEN_REFRESH_SKEW = timedelta(minutes=5)

_RETRYABLE_WECHAT_CODES = {-1, 45009}
_token_locks: WeakValueDictionary[tuple[str, int], asyncio.Lock] = WeakValueDictionary()
_ResponseT = TypeVar("_ResponseT", bound=BaseModel)


class WechatIntegrationError(RuntimeError):
    """Normalized failure from the WeChat Open Platform boundary."""

    def __init__(
        self,
        message: str,
        *,
        code: str | int | None,
        retryable: bool,
        rid: str | None,
        endpoint: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.error_code = code
        self.retryable = retryable
        self.rid = rid
        self.endpoint = endpoint


class _ResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _WechatErrorResponse(_ResponseModel):
    errcode: StrictInt
    errmsg: StrictStr | None = None
    rid: StrictStr | None = None


class _ComponentTokenResponse(_ResponseModel):
    component_access_token: StrictStr = Field(min_length=1)
    expires_in: StrictInt = Field(gt=0)
    errcode: StrictInt | None = None
    errmsg: StrictStr | None = None
    rid: StrictStr | None = None


class _AuthorizerTokenResponse(_ResponseModel):
    authorizer_access_token: StrictStr = Field(min_length=1)
    expires_in: StrictInt = Field(gt=0)
    authorizer_refresh_token: StrictStr | None = Field(default=None, min_length=1)
    errcode: StrictInt | None = None
    errmsg: StrictStr | None = None
    rid: StrictStr | None = None


class _FuncscopeCategory(_ResponseModel):
    id: StrictInt


class _FuncInfo(_ResponseModel):
    funcscope_category: _FuncscopeCategory


class _AuthorizationInfo(_ResponseModel):
    authorizer_appid: StrictStr = Field(min_length=1)
    authorizer_access_token: StrictStr | None = Field(default=None, min_length=1)
    authorizer_refresh_token: StrictStr | None = Field(default=None, min_length=1)
    expires_in: StrictInt | None = Field(default=None, gt=0)
    func_info: list[_FuncInfo]


class _QueryAuthResponse(_ResponseModel):
    authorization_info: _AuthorizationInfo
    errcode: StrictInt | None = None
    errmsg: StrictStr | None = None
    rid: StrictStr | None = None


def _lock_for(kind: str, identifier: int) -> asyncio.Lock:
    key = (kind, identifier)
    lock = _token_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _token_locks[key] = lock
    return lock


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _token_is_fresh(ciphertext: str | None, expires_at: datetime | None) -> bool:
    return bool(
        ciphertext
        and expires_at
        and _utc(expires_at) > datetime.now(UTC) + TOKEN_REFRESH_SKEW
    )


def _safe_error(
    message: str,
    *,
    code: str | int | None,
    endpoint: str,
    retryable: bool = False,
    rid: str | None = None,
) -> WechatIntegrationError:
    return WechatIntegrationError(
        message,
        code=code,
        retryable=retryable,
        rid=rid,
        endpoint=endpoint,
    )


def _validate_response(
    model: type[_ResponseT],
    payload: dict[str, Any],
    *,
    endpoint: str,
) -> _ResponseT:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise _safe_error(
            "WeChat API returned an invalid response",
            code="invalid_response",
            endpoint=endpoint,
        ) from exc


def _decrypt(value: str, *, endpoint: str) -> str:
    try:
        return decrypt_credential(value)
    except CredentialEncryptionError as exc:
        raise _safe_error(
            "Stored WeChat credential cannot be decrypted",
            code="credential_decryption_failed",
            endpoint=endpoint,
        ) from exc


def _encrypt(value: str, *, endpoint: str) -> str:
    try:
        return encrypt_credential(value)
    except CredentialEncryptionError as exc:
        raise _safe_error(
            "WeChat credential cannot be encrypted",
            code="credential_encryption_failed",
            endpoint=endpoint,
        ) from exc


class WechatOpenPlatformClient:
    """Client for third-party platform token exchange and refresh flows."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = WECHAT_API_BASE_URL,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def get_component_access_token(
        self,
        session: AsyncSession,
        integration_id: int,
    ) -> str:
        async with _lock_for("component", integration_id):
            integration = await session.get(PlatformIntegration, integration_id)
            if (
                integration is None
                or integration.platform != Platform.WECHAT_OFFICIAL_ACCOUNT.value
            ):
                raise _safe_error(
                    "WeChat platform integration is not configured",
                    code="integration_not_configured",
                    endpoint=COMPONENT_TOKEN_ENDPOINT,
                )
            credential = await session.scalar(
                select(WechatComponentCredential).where(
                    WechatComponentCredential.platform_integration_id == integration_id
                )
            )
            if credential is None or not credential.component_verify_ticket_encrypted:
                raise _safe_error(
                    "WeChat component verify ticket is not configured",
                    code="ticket_not_configured",
                    endpoint=COMPONENT_TOKEN_ENDPOINT,
                )
            if _token_is_fresh(
                credential.component_access_token_encrypted,
                credential.token_expires_at,
            ):
                return _decrypt(
                    credential.component_access_token_encrypted or "",
                    endpoint=COMPONENT_TOKEN_ENDPOINT,
                )

            try:
                component_secret = resolve_secret_ref(
                    integration.client_secret_ref,
                    platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
                )
            except SecretNotConfiguredError as exc:
                error = _safe_error(
                    "WeChat component secret reference is not configured",
                    code="secret_not_configured",
                    endpoint=COMPONENT_TOKEN_ENDPOINT,
                )
                credential.last_error = str(error)
                await session.commit()
                raise error from exc

            try:
                payload = await self._post_json(
                    COMPONENT_TOKEN_ENDPOINT,
                    json={
                        "component_appid": integration.client_key,
                        "component_appsecret": component_secret,
                        "component_verify_ticket": _decrypt(
                            credential.component_verify_ticket_encrypted,
                            endpoint=COMPONENT_TOKEN_ENDPOINT,
                        ),
                    },
                )
                response = _validate_response(
                    _ComponentTokenResponse,
                    payload,
                    endpoint=COMPONENT_TOKEN_ENDPOINT,
                )
                credential.component_access_token_encrypted = _encrypt(
                    response.component_access_token,
                    endpoint=COMPONENT_TOKEN_ENDPOINT,
                )
                credential.token_expires_at = datetime.now(UTC) + timedelta(
                    seconds=response.expires_in
                )
                credential.last_error = None
                await session.commit()
                return response.component_access_token
            except WechatIntegrationError as exc:
                credential.last_error = str(exc)
                await session.commit()
                raise

    async def get_authorizer_access_token(
        self,
        session: AsyncSession,
        account_id: int,
    ) -> str:
        async with _lock_for("authorizer", account_id):
            auth = await session.scalar(
                select(PlatformAccountAuth).where(
                    PlatformAccountAuth.account_id == account_id,
                    PlatformAccountAuth.platform == Platform.WECHAT_OFFICIAL_ACCOUNT.value,
                )
            )
            if auth is None:
                raise _safe_error(
                    "WeChat authorizer is not configured",
                    code="authorizer_not_configured",
                    endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                )
            if _token_is_fresh(auth.access_token_encrypted, auth.token_expires_at):
                return _decrypt(
                    auth.access_token_encrypted or "",
                    endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                )
            if not auth.external_open_id or not auth.refresh_token_encrypted:
                raise _safe_error(
                    "WeChat authorizer refresh credential is not configured",
                    code="authorizer_refresh_not_configured",
                    endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                )

            integration = await session.scalar(
                select(PlatformIntegration).where(
                    PlatformIntegration.org_id == auth.org_id,
                    PlatformIntegration.platform == Platform.WECHAT_OFFICIAL_ACCOUNT.value,
                )
            )
            if integration is None:
                raise _safe_error(
                    "WeChat platform integration is not configured",
                    code="integration_not_configured",
                    endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                )

            try:
                component_token = await self.get_component_access_token(session, integration.id)
                refresh_token = _decrypt(
                    auth.refresh_token_encrypted,
                    endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                )
                payload = await self._post_json(
                    AUTHORIZER_TOKEN_ENDPOINT,
                    params={"component_access_token": component_token},
                    json={
                        "component_appid": integration.client_key,
                        "authorizer_appid": auth.external_open_id,
                        "authorizer_refresh_token": refresh_token,
                    },
                )
                response = _validate_response(
                    _AuthorizerTokenResponse,
                    payload,
                    endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                )
                auth.access_token_encrypted = _encrypt(
                    response.authorizer_access_token,
                    endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                )
                if response.authorizer_refresh_token:
                    auth.refresh_token_encrypted = _encrypt(
                        response.authorizer_refresh_token,
                        endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                    )
                auth.token_expires_at = datetime.now(UTC) + timedelta(
                    seconds=response.expires_in
                )
                auth.auth_status = "authorized"
                auth.last_error = None
                await session.commit()
                return response.authorizer_access_token
            except WechatIntegrationError as exc:
                auth.last_error = str(exc)
                await session.commit()
                raise

    async def exchange_authorization_code(
        self,
        session: AsyncSession,
        integration_id: int,
        authorization_code: str,
    ) -> WechatAuthorizationGrant:
        integration = await session.get(PlatformIntegration, integration_id)
        if (
            integration is None
            or integration.platform != Platform.WECHAT_OFFICIAL_ACCOUNT.value
        ):
            raise _safe_error(
                "WeChat platform integration is not configured",
                code="integration_not_configured",
                endpoint=QUERY_AUTH_ENDPOINT,
            )
        if not authorization_code:
            raise _safe_error(
                "WeChat authorization code is empty",
                code="authorization_code_required",
                endpoint=QUERY_AUTH_ENDPOINT,
            )
        component_token = await self.get_component_access_token(session, integration_id)
        payload = await self._post_json(
            QUERY_AUTH_ENDPOINT,
            params={"component_access_token": component_token},
            json={
                "component_appid": integration.client_key,
                "authorization_code": authorization_code,
            },
        )
        response = _validate_response(
            _QueryAuthResponse,
            payload,
            endpoint=QUERY_AUTH_ENDPOINT,
        )
        info = response.authorization_info
        return WechatAuthorizationGrant(
            authorizer_appid=info.authorizer_appid,
            authorizer_access_token=info.authorizer_access_token,
            authorizer_refresh_token=info.authorizer_refresh_token,
            expires_in=info.expires_in,
            func_info=[item.funcscope_category.id for item in info.func_info],
        )

    async def _post_json(
        self,
        endpoint: str,
        *,
        json: dict[str, Any],
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            if self._client is not None:
                response = await self._client.post(
                    f"{self._base_url}{endpoint}",
                    params=params,
                    json=json,
                )
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        f"{self._base_url}{endpoint}",
                        params=params,
                        json=json,
                    )
        except httpx.RequestError as exc:
            raise _safe_error(
                "WeChat API request failed",
                code="request_failed",
                retryable=True,
                endpoint=endpoint,
            ) from exc

        payload: object
        try:
            payload = response.json()
        except ValueError as exc:
            if response.is_error:
                raise _safe_error(
                    "WeChat API request failed",
                    code=f"http_{response.status_code}",
                    retryable=response.status_code == 429 or response.status_code >= 500,
                    endpoint=endpoint,
                ) from exc
            raise _safe_error(
                "WeChat API returned an invalid response",
                code="invalid_response",
                endpoint=endpoint,
            ) from exc
        if not isinstance(payload, dict):
            raise _safe_error(
                "WeChat API returned an invalid response",
                code="invalid_response",
                endpoint=endpoint,
            )

        errcode = payload.get("errcode")
        if errcode not in (None, 0):
            error_response = _validate_response(
                _WechatErrorResponse,
                payload,
                endpoint=endpoint,
            )
            raise _safe_error(
                f"WeChat API returned error {error_response.errcode}",
                code=error_response.errcode,
                retryable=error_response.errcode in _RETRYABLE_WECHAT_CODES,
                rid=error_response.rid,
                endpoint=endpoint,
            )
        if response.is_error:
            raise _safe_error(
                "WeChat API request failed",
                code=f"http_{response.status_code}",
                retryable=response.status_code == 429 or response.status_code >= 500,
                endpoint=endpoint,
            )
        return payload
