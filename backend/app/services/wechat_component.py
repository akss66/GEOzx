"""Secure WeChat Open Platform component and authorizer token lifecycle."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, TypeVar
from weakref import WeakValueDictionary

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

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
_sqlite_token_locks: WeakValueDictionary[tuple[str, int], asyncio.Lock] = (
    WeakValueDictionary()
)
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


def _sqlite_lock_for(kind: str, identifier: int) -> asyncio.Lock:
    key = (kind, identifier)
    lock = _sqlite_token_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _sqlite_token_locks[key] = lock
    return lock


def _advisory_lock_key(kind: str, identifier: int) -> int:
    digest = hashlib.blake2b(
        f"wechat-token:{kind}:{identifier}".encode(),
        digest_size=8,
        person=b"dyflow-wx-token",
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


class _TokenTransactionCoordinator(Protocol):
    def transaction(
        self,
        session: AsyncSession,
        kind: str,
        identifier: int,
    ) -> AbstractAsyncContextManager[None]: ...


class _DatabaseTokenCoordinator:
    """Own a token transaction and coordinate it across PostgreSQL workers."""

    @asynccontextmanager
    async def transaction(
        self,
        session: AsyncSession,
        kind: str,
        identifier: int,
    ) -> AsyncIterator[None]:
        bind = session.bind
        if bind is None:
            raise RuntimeError("token session is not bound to a database")
        if bind.dialect.name == "postgresql":
            async with session.begin():
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": _advisory_lock_key(kind, identifier)},
                )
                yield
            return
        if bind.dialect.name != "sqlite":
            raise RuntimeError(
                f"unsupported token coordination database: {bind.dialect.name}"
            )
        # SQLite is used only by deterministic unit tests. Production PostgreSQL
        # never relies on this process-local fallback.
        async with _sqlite_lock_for(kind, identifier):
            async with session.begin():
                yield


@asynccontextmanager
async def _owned_session(
    caller_session: AsyncSession,
    *,
    endpoint: str,
) -> AsyncIterator[AsyncSession]:
    bind = caller_session.bind
    if not isinstance(bind, AsyncEngine):
        raise _safe_error(
            "WeChat token storage requires an independently bound database engine",
            code="token_storage_unavailable",
            endpoint=endpoint,
            retryable=True,
        )
    maker = async_sessionmaker(bind, expire_on_commit=False, autoflush=False)
    async with maker() as token_session:
        yield token_session


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
        _coordinator: _TokenTransactionCoordinator | None = None,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._coordinator = _coordinator or _DatabaseTokenCoordinator()

    async def get_component_access_token(
        self,
        session: AsyncSession,
        integration_id: int,
    ) -> str:
        token: str | None = None
        failure: WechatIntegrationError | None = None
        async with _owned_session(session, endpoint=COMPONENT_TOKEN_ENDPOINT) as token_session:
            async with self._coordinator.transaction(
                token_session,
                "component",
                integration_id,
            ):
                integration = await token_session.get(
                    PlatformIntegration,
                    integration_id,
                    populate_existing=True,
                )
                if (
                    integration is None
                    or integration.platform != Platform.WECHAT_OFFICIAL_ACCOUNT.value
                ):
                    raise _safe_error(
                        "WeChat platform integration is not configured",
                        code="integration_not_configured",
                        endpoint=COMPONENT_TOKEN_ENDPOINT,
                    )
                credential = await token_session.scalar(
                    select(WechatComponentCredential)
                    .where(
                        WechatComponentCredential.platform_integration_id
                        == integration_id
                    )
                    .execution_options(populate_existing=True)
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
                    token = _decrypt(
                        credential.component_access_token_encrypted or "",
                        endpoint=COMPONENT_TOKEN_ENDPOINT,
                    )
                else:
                    old_token = credential.component_access_token_encrypted
                    old_expiry = credential.token_expires_at
                    try:
                        component_secret = resolve_secret_ref(
                            integration.client_secret_ref,
                            platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
                        )
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
                        encrypted_token = _encrypt(
                            response.component_access_token,
                            endpoint=COMPONENT_TOKEN_ENDPOINT,
                        )
                        new_expiry = datetime.now(UTC) + timedelta(
                            seconds=response.expires_in
                        )
                        token_match = (
                            WechatComponentCredential.component_access_token_encrypted.is_(
                                None
                            )
                            if old_token is None
                            else WechatComponentCredential.component_access_token_encrypted
                            == old_token
                        )
                        expiry_match = (
                            WechatComponentCredential.token_expires_at.is_(None)
                            if old_expiry is None
                            else WechatComponentCredential.token_expires_at == old_expiry
                        )
                        cas_result = await token_session.execute(
                            update(WechatComponentCredential)
                            .where(
                                WechatComponentCredential.id == credential.id,
                                token_match,
                                expiry_match,
                            )
                            .values(
                                component_access_token_encrypted=encrypted_token,
                                token_expires_at=new_expiry,
                                last_error=None,
                            )
                            .execution_options(synchronize_session=False)
                        )
                        if getattr(cas_result, "rowcount", 0) == 1:
                            token = response.component_access_token
                        else:
                            fresh = await token_session.get(
                                WechatComponentCredential,
                                credential.id,
                                populate_existing=True,
                            )
                            if fresh is not None and _token_is_fresh(
                                fresh.component_access_token_encrypted,
                                fresh.token_expires_at,
                            ):
                                token = _decrypt(
                                    fresh.component_access_token_encrypted or "",
                                    endpoint=COMPONENT_TOKEN_ENDPOINT,
                                )
                            else:
                                failure = _safe_error(
                                    "WeChat component token state changed during refresh",
                                    code="token_state_changed",
                                    retryable=True,
                                    endpoint=COMPONENT_TOKEN_ENDPOINT,
                                )
                    except SecretNotConfiguredError:
                        failure = _safe_error(
                            "WeChat component secret reference is not configured",
                            code="secret_not_configured",
                            endpoint=COMPONENT_TOKEN_ENDPOINT,
                        )
                    except WechatIntegrationError as exc:
                        failure = exc
                    if failure is not None:
                        credential.last_error = str(failure)
        if failure is not None:
            raise failure
        if token is None:
            raise _safe_error(
                "WeChat component token refresh produced no token",
                code="token_refresh_failed",
                retryable=True,
                endpoint=COMPONENT_TOKEN_ENDPOINT,
            )
        return token

    async def get_authorizer_access_token(
        self,
        session: AsyncSession,
        account_id: int,
    ) -> str:
        integration_id: int | None = None
        async with _owned_session(session, endpoint=AUTHORIZER_TOKEN_ENDPOINT) as lookup:
            async with lookup.begin():
                auth = await lookup.scalar(
                    select(PlatformAccountAuth)
                    .where(
                        PlatformAccountAuth.account_id == account_id,
                        PlatformAccountAuth.platform
                        == Platform.WECHAT_OFFICIAL_ACCOUNT.value,
                    )
                    .execution_options(populate_existing=True)
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
                integration = await lookup.scalar(
                    select(PlatformIntegration).where(
                        PlatformIntegration.org_id == auth.org_id,
                        PlatformIntegration.platform
                        == Platform.WECHAT_OFFICIAL_ACCOUNT.value,
                    )
                )
                if integration is not None:
                    integration_id = integration.id
        if integration_id is None:
            raise _safe_error(
                "WeChat platform integration is not configured",
                code="integration_not_configured",
                endpoint=AUTHORIZER_TOKEN_ENDPOINT,
            )

        component_token = await self.get_component_access_token(session, integration_id)
        token: str | None = None
        failure: WechatIntegrationError | None = None
        async with _owned_session(session, endpoint=AUTHORIZER_TOKEN_ENDPOINT) as token_session:
            async with self._coordinator.transaction(
                token_session,
                "authorizer",
                account_id,
            ):
                auth = await token_session.scalar(
                    select(PlatformAccountAuth)
                    .where(
                        PlatformAccountAuth.account_id == account_id,
                        PlatformAccountAuth.platform
                        == Platform.WECHAT_OFFICIAL_ACCOUNT.value,
                    )
                    .execution_options(populate_existing=True)
                )
                if auth is None:
                    raise _safe_error(
                        "WeChat authorizer is not configured",
                        code="authorizer_not_configured",
                        endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                    )
                if _token_is_fresh(auth.access_token_encrypted, auth.token_expires_at):
                    token = _decrypt(
                        auth.access_token_encrypted or "",
                        endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                    )
                elif not auth.external_open_id or not auth.refresh_token_encrypted:
                    raise _safe_error(
                        "WeChat authorizer refresh credential is not configured",
                        code="authorizer_refresh_not_configured",
                        endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                    )
                else:
                    integration = await token_session.get(
                        PlatformIntegration,
                        integration_id,
                        populate_existing=True,
                    )
                    if (
                        integration is None
                        or integration.org_id != auth.org_id
                        or integration.platform
                        != Platform.WECHAT_OFFICIAL_ACCOUNT.value
                    ):
                        raise _safe_error(
                            "WeChat platform integration is not configured",
                            code="integration_not_configured",
                            endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                        )
                    old_access_token = auth.access_token_encrypted
                    old_refresh_token = auth.refresh_token_encrypted
                    old_expiry = auth.token_expires_at
                    old_auth_status = auth.auth_status
                    try:
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
                        encrypted_access_token = _encrypt(
                            response.authorizer_access_token,
                            endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                        )
                        encrypted_refresh_token = old_refresh_token
                        if response.authorizer_refresh_token:
                            encrypted_refresh_token = _encrypt(
                                response.authorizer_refresh_token,
                                endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                            )
                        new_expiry = datetime.now(UTC) + timedelta(
                            seconds=response.expires_in
                        )
                        access_match = (
                            PlatformAccountAuth.access_token_encrypted.is_(None)
                            if old_access_token is None
                            else PlatformAccountAuth.access_token_encrypted
                            == old_access_token
                        )
                        refresh_match = (
                            PlatformAccountAuth.refresh_token_encrypted.is_(None)
                            if old_refresh_token is None
                            else PlatformAccountAuth.refresh_token_encrypted
                            == old_refresh_token
                        )
                        expiry_match = (
                            PlatformAccountAuth.token_expires_at.is_(None)
                            if old_expiry is None
                            else PlatformAccountAuth.token_expires_at == old_expiry
                        )
                        cas_result = await token_session.execute(
                            update(PlatformAccountAuth)
                            .where(
                                PlatformAccountAuth.id == auth.id,
                                access_match,
                                refresh_match,
                                expiry_match,
                                PlatformAccountAuth.auth_status == old_auth_status,
                            )
                            .values(
                                access_token_encrypted=encrypted_access_token,
                                refresh_token_encrypted=encrypted_refresh_token,
                                token_expires_at=new_expiry,
                                auth_status="authorized",
                                last_error=None,
                            )
                            .execution_options(synchronize_session=False)
                        )
                        if getattr(cas_result, "rowcount", 0) == 1:
                            token = response.authorizer_access_token
                        else:
                            fresh = await token_session.get(
                                PlatformAccountAuth,
                                auth.id,
                                populate_existing=True,
                            )
                            if (
                                fresh is not None
                                and fresh.auth_status == "authorized"
                                and _token_is_fresh(
                                    fresh.access_token_encrypted,
                                    fresh.token_expires_at,
                                )
                            ):
                                token = _decrypt(
                                    fresh.access_token_encrypted or "",
                                    endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                                )
                            else:
                                failure = _safe_error(
                                    "WeChat authorizer token state changed during refresh",
                                    code="token_state_changed",
                                    retryable=True,
                                    endpoint=AUTHORIZER_TOKEN_ENDPOINT,
                                )
                    except WechatIntegrationError as exc:
                        failure = exc
                    if failure is not None:
                        auth.last_error = str(failure)
        if failure is not None:
            raise failure
        if token is None:
            raise _safe_error(
                "WeChat authorizer token refresh produced no token",
                code="token_refresh_failed",
                retryable=True,
                endpoint=AUTHORIZER_TOKEN_ENDPOINT,
            )
        return token

    async def exchange_authorization_code(
        self,
        session: AsyncSession,
        integration_id: int,
        authorization_code: str,
    ) -> WechatAuthorizationGrant:
        if not authorization_code:
            raise _safe_error(
                "WeChat authorization code is empty",
                code="authorization_code_required",
                endpoint=QUERY_AUTH_ENDPOINT,
            )
        async with _owned_session(session, endpoint=QUERY_AUTH_ENDPOINT) as lookup:
            async with lookup.begin():
                integration = await lookup.get(
                    PlatformIntegration,
                    integration_id,
                    populate_existing=True,
                )
                if (
                    integration is None
                    or integration.platform != Platform.WECHAT_OFFICIAL_ACCOUNT.value
                ):
                    raise _safe_error(
                        "WeChat platform integration is not configured",
                        code="integration_not_configured",
                        endpoint=QUERY_AUTH_ENDPOINT,
                    )
                component_appid = integration.client_key
        component_token = await self.get_component_access_token(session, integration_id)
        payload = await self._post_json(
            QUERY_AUTH_ENDPOINT,
            params={"component_access_token": component_token},
            json={
                "component_appid": component_appid,
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
        if type(errcode) is int and errcode != 0:
            rid = payload.get("rid")
            raise _safe_error(
                f"WeChat API returned error {errcode}",
                code=errcode,
                retryable=errcode in _RETRYABLE_WECHAT_CODES,
                rid=rid if isinstance(rid, str) else None,
                endpoint=endpoint,
            )
        if errcode not in (None, 0):
            raise _safe_error(
                "WeChat API returned an invalid response",
                code="invalid_response",
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
