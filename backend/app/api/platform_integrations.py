"""Official platform integration configuration APIs."""

import base64
import hashlib
import os
import secrets
import struct
from binascii import Error as BinasciiError
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import Annotated
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from uuid import uuid4
from xml.etree import ElementTree

import httpx
import jwt
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import AdminUser, CurrentUser
from app.core.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)
from app.db import get_session
from app.integrations.douyin import (
    DEFAULT_DOUYIN_SECRET_REF,
    DouyinIntegrationError,
    SecretNotConfiguredError,
    build_douyin_authorization_url,
    create_js_signature,
    exchange_douyin_access_token,
    fetch_douyin_user_info,
    get_douyin_jsb_ticket,
    normalize_douyin_user_profile,
    refresh_douyin_access_token,
    resolve_douyin_account_token_ref,
    resolve_secret_ref,
)
from app.integrations.douyin_capabilities import (
    DOUYIN_CAPABILITY_BY_KEY,
    diagnose_douyin_capabilities,
)
from app.models import (
    Account,
    AccountGroup,
    Client,
    Event,
    PlatformAccountAuth,
    PlatformIntegration,
    Project,
    WechatComponentCredential,
)
from app.models.enums import Platform
from app.schemas.platform import (
    DouyinAccountCapabilitiesOut,
    DouyinAuthorizeOut,
    DouyinAuthorizeRequest,
    DouyinDataSyncOut,
    DouyinIncrementalAuthorizeRequest,
    DouyinJsSignatureOut,
    DouyinJsSignatureRequest,
    DouyinOAuthCallbackOut,
    DouyinOAuthCompleteRequest,
    DouyinScanAddRequest,
    DouyinTrialWhitelistOut,
    PlatformIntegrationOut,
    UpsertPlatformIntegrationRequest,
    WechatAuthorizationSessionOut,
    WechatAuthorizationSessionRequest,
    WechatPreAuthCodeResponse,
)
from app.services.wechat_component import WechatIntegrationError, WechatOpenPlatformClient

router = APIRouter(tags=["platform-integrations"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

DOUYIN_OFFICIAL_DOCS = [
    "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/sdk/web-app/js/js-access",
    "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/sdk/web-app/js/signature",
    "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/sdk/web-app/js/js-bridge/call-user-permission-page",
    "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/sdk/web-app/h5/share-to-h5",
    "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/sdk/web-app/web/permission",
    "https://developer.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/list",
]

DOUYIN_DEFAULT_CAPABILITIES = {
    "web_oauth": "required",
    "js_sdk_signature": "required",
    "js_bridge_auth": "optional",
    "h5_share": "optional",
    "openapi": "required",
}

DEFAULT_DOUYIN_SCOPES = ["user_info"]
DOUYIN_TRIAL_WHITELIST_SCOPE = "trial.whitelist"
DOUYIN_TRIAL_WHITELIST_SCOPES = [DOUYIN_TRIAL_WHITELIST_SCOPE, "user_info"]

WECHAT_PRE_AUTH_CODE_ENDPOINT = (
    "https://api.weixin.qq.com/cgi-bin/component/api_create_preauthcode"
)
WECHAT_COMPONENT_LOGIN_URL = "https://mp.weixin.qq.com/cgi-bin/componentloginpage"
WECHAT_AUTHORIZATION_STATE_TTL = timedelta(minutes=10)
WECHAT_CALLBACK_TIMESTAMP_WINDOW_SECONDS = 300
WECHAT_CALLBACK_MAX_BODY_BYTES = 65_536
_WECHAT_STATE_CREATED = "wechat.authorization.session.created"
_WECHAT_STATE_CONSUMED = "wechat.authorization.session.consumed"
_WECHAT_EVENT_TYPES = frozenset(
    {"component_verify_ticket", "authorized", "updateauthorized", "unauthorized"}
)


def _default_capabilities(platform: Platform) -> dict[str, str]:
    if platform == Platform.DOUYIN:
        return dict(DOUYIN_DEFAULT_CAPABILITIES)
    return {"official_openapi": "to_verify", "manual_fallback": "enabled"}


def _default_client_secret_ref(platform: Platform) -> str | None:
    if platform == Platform.DOUYIN:
        return DEFAULT_DOUYIN_SECRET_REF
    return None


def _default_client_key(platform: Platform) -> str | None:
    if platform == Platform.DOUYIN and settings.douyin_client_key:
        return settings.douyin_client_key
    return None


def _official_docs(platform: Platform) -> list[str]:
    if platform == Platform.DOUYIN:
        return list(DOUYIN_OFFICIAL_DOCS)
    return []


def _default_out(platform: Platform) -> PlatformIntegrationOut:
    return PlatformIntegrationOut(
        id=None,
        platform=platform,
        status="not_configured",
        client_key=_default_client_key(platform),
        client_secret_configured=bool(_default_client_secret_ref(platform)),
        redirect_uri=None,
        js_sdk_domain=None,
        auth_status="not_configured",
        data_sync_status="not_configured",
        scopes=[],
        capabilities=_default_capabilities(platform),
        official_docs=_official_docs(platform),
        note=None,
        created_at=None,
        updated_at=None,
    )


def _to_out(row: PlatformIntegration) -> PlatformIntegrationOut:
    return PlatformIntegrationOut(
        id=row.id,
        platform=Platform(row.platform),
        status=row.status,
        client_key=row.client_key,
        client_secret_configured=row.client_secret_configured,
        redirect_uri=row.redirect_uri,
        js_sdk_domain=row.js_sdk_domain,
        auth_status=row.auth_status,
        data_sync_status=row.data_sync_status,
        scopes=row.scopes or [],
        capabilities=row.capabilities or _default_capabilities(Platform(row.platform)),
        official_docs=row.official_docs or _official_docs(Platform(row.platform)),
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/platform-integrations", response_model=list[PlatformIntegrationOut])
async def list_platform_integrations(
    admin: AdminUser, session: SessionDep
) -> list[PlatformIntegrationOut]:
    rows = (
        await session.scalars(
            select(PlatformIntegration)
            .where(PlatformIntegration.org_id == admin.org_id)
            .order_by(PlatformIntegration.platform)
        )
    ).all()
    by_platform = {Platform(row.platform): row for row in rows}
    return [
        _to_out(by_platform[platform]) if platform in by_platform else _default_out(platform)
        for platform in Platform
    ]


async def _get_integration_or_404(
    session: AsyncSession, org_id: int, platform: Platform
) -> PlatformIntegration:
    row = await session.scalar(
        select(PlatformIntegration).where(
            PlatformIntegration.org_id == org_id,
            PlatformIntegration.platform == platform.value,
        )
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="platform integration is not configured",
        )
    return row


def _require_douyin_app(integration: PlatformIntegration) -> None:
    if not integration.client_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="client_key is required",
        )


def _create_oauth_state(
    *,
    org_id: int,
    account_id: int | None,
    flow: str = "bind_existing",
    nickname: str | None = None,
    group_id: int | None = None,
    project_id: int | None = None,
    initiated_by: int | None = None,
) -> str:
    now = datetime.now(UTC)
    payload = {
        "purpose": "douyin_oauth",
        "flow": flow,
        "org_id": org_id,
        "account_id": account_id,
        "nickname": nickname,
        "group_id": group_id,
        "project_id": project_id,
        "initiated_by": initiated_by,
        "iat": now,
        "exp": now + timedelta(minutes=15),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode_oauth_state(state: str) -> dict:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid state"
        ) from exc
    if payload.get("purpose") != "douyin_oauth":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid state")
    return payload


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _require_worker_secret(worker_secret: str | None, authorization: str | None = None) -> None:
    configured_secret = settings.douyin_oauth_worker_secret
    provided_secret = worker_secret or _extract_bearer_token(authorization)
    if not configured_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Douyin OAuth worker bridge is not configured",
        )
    if not provided_secret or not compare_digest(provided_secret, configured_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid worker secret",
        )


def _resolve_client_secret(integration: PlatformIntegration) -> str:
    try:
        return resolve_secret_ref(
            integration.client_secret_ref or DEFAULT_DOUYIN_SECRET_REF,
            platform=Platform(integration.platform),
        )
    except SecretNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _parse_scopes(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _has_text(value: str | None) -> bool:
    return bool(value and value.strip())


def _has_app_credentials(row: PlatformIntegration) -> bool:
    return _has_text(row.client_key) and _has_text(row.client_secret_ref)


def _apply_platform_status_defaults(row: PlatformIntegration) -> None:
    if row.status == "not_configured" and _has_app_credentials(row):
        row.status = "configured"
    if row.status == "configured" and row.auth_status in {None, "not_configured"}:
        row.auth_status = "unauthorized"


async def _get_owned_douyin_account(
    session: AsyncSession, account_id: int, org_id: int
) -> Account:
    account = await session.get(Account, account_id)
    if account is None or account.org_id != org_id or account.platform != Platform.DOUYIN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Douyin account not found"
        )
    return account


async def _validate_group(session: AsyncSession, group_id: int | None, org_id: int) -> None:
    if group_id is None:
        return
    group = await session.get(AccountGroup, group_id)
    if group is None or group.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="group not found")


async def _validate_project(session: AsyncSession, project_id: int | None, org_id: int) -> None:
    if project_id is None:
        return
    project = await session.get(Project, project_id)
    if project is None or project.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")


def _apply_authorized_account_meta(account: Account, scopes: list[str]) -> None:
    meta = dict(account.auth or {})
    meta.update(
        {
            "integration_status": "connected",
            "auth_status": "authorized",
            "data_sync_status": "pending",
            "scopes": scopes,
        }
    )
    account.auth = meta


async def _get_or_create_douyin_scan_account(
    *,
    session: AsyncSession,
    org_id: int,
    external_open_id: str,
    nickname: str | None,
    group_id: int | None,
    project_id: int | None,
    scopes: list[str],
) -> Account:
    account = await session.scalar(
        select(Account)
        .where(
            Account.org_id == org_id,
            Account.platform == Platform.DOUYIN,
            Account.external_account_id == external_open_id,
        )
        .order_by(Account.id)
    )
    if account is None:
        account = Account(
            org_id=org_id,
            platform=Platform.DOUYIN,
            nickname=nickname or f"抖音账号 {external_open_id[-6:]}",
            external_account_id=external_open_id,
            group_id=group_id,
            project_id=project_id,
        )
        session.add(account)
        await session.flush()
    else:
        if nickname and account.nickname.startswith("抖音账号 "):
            account.nickname = nickname
        if group_id is not None:
            account.group_id = group_id
        if project_id is not None:
            account.project_id = project_id
    _apply_authorized_account_meta(account, scopes)
    return account


def _wechat_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _append_query_value(url: str, key: str, value: str) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append((key, value))
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


async def _request_wechat_pre_auth_code(
    component_appid: str,
    component_access_token: str,
) -> str:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                WECHAT_PRE_AUTH_CODE_ENDPOINT,
                params={"component_access_token": component_access_token},
                json={"component_appid": component_appid},
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WeChat pre-authorization service is unavailable",
        ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="WeChat returned an invalid pre-authorization response",
        ) from exc
    if response.is_error or not isinstance(payload, dict) or payload.get("errcode") not in (
        None,
        0,
    ):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="WeChat pre-authorization request failed",
        )
    try:
        return WechatPreAuthCodeResponse.model_validate(payload).pre_auth_code
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="WeChat returned an invalid pre-authorization response",
        ) from exc


async def _validate_wechat_authorization_targets(
    session: AsyncSession,
    *,
    org_id: int,
    body: WechatAuthorizationSessionRequest,
) -> None:
    client = await session.get(Client, body.client_id) if body.client_id else None
    if body.client_id and (client is None or client.org_id != org_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="client not found")
    project = await session.get(Project, body.project_id) if body.project_id else None
    if body.project_id and (project is None or project.org_id != org_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    if client is not None and project is not None and project.client_id not in (
        None,
        client.id,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="project does not belong to client",
        )


@router.post(
    "/platform-integrations/wechat/authorization-sessions",
    response_model=WechatAuthorizationSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_wechat_authorization_session(
    body: WechatAuthorizationSessionRequest,
    admin: AdminUser,
    session: SessionDep,
) -> WechatAuthorizationSessionOut:
    integration = await _get_integration_or_404(
        session, admin.org_id, Platform.WECHAT_OFFICIAL_ACCOUNT
    )
    if not integration.client_key or not integration.redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="component AppID and redirect URI are required",
        )
    await _validate_wechat_authorization_targets(
        session, org_id=admin.org_id, body=body
    )
    component_token = await WechatOpenPlatformClient().get_component_access_token(
        session, integration.id
    )
    pre_auth_code = await _request_wechat_pre_auth_code(
        integration.client_key, component_token
    )

    now = datetime.now(UTC)
    expires_at = now + WECHAT_AUTHORIZATION_STATE_TTL
    state_id = uuid4().hex
    state = secrets.token_urlsafe(32)
    state_digest = _wechat_hash(state)
    session.add(
        Event(
            type=_WECHAT_STATE_CREATED,
            org_id=admin.org_id,
            idempotency_key=state_digest,
            payload={
                "state_id": state_id,
                "org_id": admin.org_id,
                "initiated_by_id": admin.id,
                "client_id": body.client_id,
                "project_id": body.project_id,
                "knowledge_base_id": body.knowledge_base_id,
                "issued_at": now.isoformat(),
                "expires_at": expires_at.isoformat(),
            },
        )
    )
    await session.commit()

    callback_url = _append_query_value(integration.redirect_uri, "state", state)
    login_params = {
        "component_appid": integration.client_key,
        "pre_auth_code": pre_auth_code,
        "redirect_uri": callback_url,
        "auth_type": "3",
    }
    authorization_url = f"{WECHAT_COMPONENT_LOGIN_URL}?{urlencode(login_params)}"
    return WechatAuthorizationSessionOut(
        authorization_url=authorization_url,
        expires_at=expires_at,
        state_id=state_id,
    )


async def _consume_wechat_authorization_state(
    session: AsyncSession,
    state_value: str,
) -> dict:
    state_digest = _wechat_hash(state_value)
    created = await session.scalar(
        select(Event).where(
            Event.type == _WECHAT_STATE_CREATED,
            Event.idempotency_key == state_digest,
        )
    )
    if created is None or not isinstance(created.payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid state")
    payload = dict(created.payload)
    try:
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        org_id = int(payload["org_id"])
        state_id = str(payload["state_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid state"
        ) from exc
    if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expired state")

    consumed_key = _wechat_hash(f"wechat-state-consumed:{state_digest}")
    if await session.scalar(
        select(Event.id).where(
            Event.type == _WECHAT_STATE_CONSUMED,
            Event.idempotency_key == consumed_key,
        )
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="state already used")
    session.add(
        Event(
            type=_WECHAT_STATE_CONSUMED,
            org_id=org_id,
            idempotency_key=consumed_key,
            payload={"state_id": state_id, "org_id": org_id},
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="state already used"
        ) from exc
    return payload


async def _upsert_wechat_authorization(
    session: AsyncSession,
    *,
    integration: PlatformIntegration,
    state_payload: dict,
    grant,
) -> None:
    org_id = integration.org_id
    account = await session.scalar(
        select(Account).where(
            Account.org_id == org_id,
            Account.platform == Platform.WECHAT_OFFICIAL_ACCOUNT,
            Account.external_account_id == grant.authorizer_appid,
        )
    )
    if account is None:
        account = Account(
            org_id=org_id,
            client_id=state_payload.get("client_id"),
            project_id=state_payload.get("project_id"),
            platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
            external_account_id=grant.authorizer_appid,
            nickname=f"微信公众号 {grant.authorizer_appid[-6:]}",
        )
        session.add(account)
        await session.flush()
    else:
        if state_payload.get("client_id") is not None:
            account.client_id = int(state_payload["client_id"])
        if state_payload.get("project_id") is not None:
            account.project_id = int(state_payload["project_id"])
    auth = await session.scalar(
        select(PlatformAccountAuth).where(PlatformAccountAuth.account_id == account.id)
    )
    if auth is None:
        auth = PlatformAccountAuth(
            org_id=org_id,
            account_id=account.id,
            platform=Platform.WECHAT_OFFICIAL_ACCOUNT.value,
        )
        session.add(auth)
    try:
        access_token = (
            encrypt_credential(grant.authorizer_access_token)
            if grant.authorizer_access_token
            else None
        )
        refresh_token = (
            encrypt_credential(grant.authorizer_refresh_token)
            if grant.authorizer_refresh_token
            else None
        )
    except CredentialEncryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="credential encryption is unavailable",
        ) from exc
    scopes = [str(scope) for scope in sorted(set(grant.func_info))]
    now = datetime.now(UTC)
    auth.external_open_id = grant.authorizer_appid
    auth.auth_status = "authorized"
    auth.data_sync_status = "pending"
    auth.scopes = scopes
    auth.access_token_encrypted = access_token
    auth.refresh_token_encrypted = refresh_token
    auth.token_secret_ref = None
    auth.refresh_secret_ref = None
    auth.token_expires_at = (
        now + timedelta(seconds=grant.expires_in)
        if grant.expires_in and grant.expires_in > 0
        else None
    )
    auth.last_error = None
    auth.raw_profile = {"authorizer_appid": grant.authorizer_appid}
    account.auth = {
        **(account.auth or {}),
        "integration_status": "connected",
        "auth_status": "authorized",
        "data_sync_status": "pending",
        "scopes": scopes,
    }
    integration.status = "connected"
    integration.auth_status = "authorized"
    session.add(
        Event(
            type="wechat.authorization.completed",
            org_id=org_id,
            account_id=account.id,
            payload={
                "authorizer_appid": grant.authorizer_appid,
                "scopes": scopes,
                "state_id": state_payload["state_id"],
            },
        )
    )
    await session.commit()


@router.get("/platform-integrations/wechat/oauth/callback")
async def handle_wechat_oauth_callback(
    state: Annotated[str, Query(min_length=1, max_length=512)],
    session: SessionDep,
    authorization_code: Annotated[
        str | None, Query(min_length=1, max_length=1024)
    ] = None,
    auth_code: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> RedirectResponse:
    if authorization_code and auth_code and authorization_code != auth_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="conflicting WeChat authorization codes",
        )
    code = auth_code or authorization_code
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="WeChat authorization code is required",
        )
    state_payload = await _consume_wechat_authorization_state(session, state)
    integration = await _get_integration_or_404(
        session,
        int(state_payload["org_id"]),
        Platform.WECHAT_OFFICIAL_ACCOUNT,
    )
    try:
        grant = await WechatOpenPlatformClient().exchange_authorization_code(
            session, integration.id, code
        )
    except WechatIntegrationError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.retryable
                else status.HTTP_502_BAD_GATEWAY
            ),
            detail="WeChat authorization exchange failed",
        ) from exc
    await _upsert_wechat_authorization(
        session,
        integration=integration,
        state_payload=state_payload,
        grant=grant,
    )
    return RedirectResponse("/accounts?wechat_authorization=success")


def _wechat_callback_config() -> tuple[str, bytes]:
    verify_token = os.environ.get("WECHAT_COMPONENT_VERIFY_TOKEN", "")
    encoding_aes_key = os.environ.get("WECHAT_COMPONENT_ENCODING_AES_KEY", "")
    if (
        verify_token != verify_token.strip()
        or not 3 <= len(verify_token) <= 32
        or encoding_aes_key != encoding_aes_key.strip()
        or len(encoding_aes_key) != 43
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WeChat callback security configuration is unavailable",
        )
    try:
        aes_key = base64.b64decode(f"{encoding_aes_key}=", validate=True)
    except (BinasciiError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WeChat callback security configuration is unavailable",
        ) from exc
    if len(aes_key) != 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WeChat callback security configuration is unavailable",
        )
    return verify_token, aes_key


def _parse_wechat_xml(value: bytes) -> ElementTree.Element:
    if b"<!DOCTYPE" in value.upper() or b"<!ENTITY" in value.upper():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid WeChat callback"
        )
    try:
        return ElementTree.fromstring(value)
    except ElementTree.ParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid WeChat callback"
        ) from exc


def _wechat_xml_text(
    root: ElementTree.Element,
    name: str,
    *,
    max_length: int,
) -> str:
    value = root.findtext(name)
    if value is None or not value.strip() or len(value) > max_length:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid WeChat callback"
        )
    return value.strip()


def _decrypt_wechat_callback(ciphertext: str, aes_key: bytes) -> tuple[bytes, str]:
    try:
        encrypted = base64.b64decode(ciphertext, validate=True)
        if not encrypted or len(encrypted) % 16:
            raise ValueError("invalid ciphertext length")
        decryptor = Cipher(
            algorithms.AES(aes_key), modes.CBC(aes_key[:16])
        ).decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
    except (BinasciiError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        ) from exc
    if not padded:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    padding = padded[-1]
    if (
        padding < 1
        or padding > 32
        or padding > len(padded)
        or padded[-padding:] != bytes([padding]) * padding
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    plaintext = padded[:-padding]
    if len(plaintext) < 21:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    message_length = struct.unpack("!I", plaintext[16:20])[0]
    message_end = 20 + message_length
    if message_length < 1 or message_end >= len(plaintext):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    message = plaintext[20:message_end]
    try:
        component_appid = plaintext[message_end:].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        ) from exc
    if not component_appid or len(component_appid) > 128:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    return message, component_appid


async def _wechat_integrations_for_appid(
    session: AsyncSession,
    component_appid: str,
) -> list[PlatformIntegration]:
    rows = (
        await session.scalars(
            select(PlatformIntegration).where(
                PlatformIntegration.platform
                == Platform.WECHAT_OFFICIAL_ACCOUNT.value,
                PlatformIntegration.client_key == component_appid,
            )
        )
    ).all()
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    return list(rows)


async def _apply_wechat_callback_event(
    session: AsyncSession,
    *,
    integrations: list[PlatformIntegration],
    root: ElementTree.Element,
) -> PlainTextResponse:
    component_appid = integrations[0].client_key or ""
    integration_org_ids = {integration.org_id for integration in integrations}
    info_type = _wechat_xml_text(root, "InfoType", max_length=64).lower()
    if info_type not in _WECHAT_EVENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported WeChat callback",
        )
    try:
        create_time = int(_wechat_xml_text(root, "CreateTime", max_length=20))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid WeChat callback"
        ) from exc
    if create_time <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid WeChat callback"
        )

    authorizer_appid: str | None = None
    secret_fingerprint_value = ""
    if info_type == "component_verify_ticket":
        secret_fingerprint_value = _wechat_xml_text(
            root, "ComponentVerifyTicket", max_length=1024
        )
    elif info_type in {"authorized", "updateauthorized"}:
        authorizer_appid = _wechat_xml_text(
            root, "AuthorizerAppid", max_length=128
        )
        secret_fingerprint_value = _wechat_xml_text(
            root, "AuthorizationCode", max_length=1024
        )
    else:
        authorizer_appid = _wechat_xml_text(
            root, "AuthorizerAppid", max_length=128
        )

    fingerprint = _wechat_hash(
        "\x00".join(
            (
                "wechat-callback-v1",
                component_appid,
                info_type,
                str(create_time),
                authorizer_appid or "",
                secret_fingerprint_value,
            )
        )
    )
    if await session.scalar(select(Event.id).where(Event.idempotency_key == fingerprint)):
        return PlainTextResponse("success")

    if info_type == "component_verify_ticket":
        try:
            encrypted_ticket = encrypt_credential(secret_fingerprint_value)
        except CredentialEncryptionError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="credential encryption is unavailable",
            ) from exc
        received_at = datetime.now(UTC)
        integration_ids = [integration.id for integration in integrations]
        existing_credentials = (
            await session.scalars(
                select(WechatComponentCredential).where(
                    WechatComponentCredential.platform_integration_id.in_(
                        integration_ids
                    )
                )
            )
        ).all()
        credentials_by_integration = {
            credential.platform_integration_id: credential
            for credential in existing_credentials
        }
        for integration in integrations:
            credential = credentials_by_integration.get(integration.id)
            if credential is None:
                credential = WechatComponentCredential(
                    platform_integration_id=integration.id
                )
                session.add(credential)
            credential.component_verify_ticket_encrypted = encrypted_ticket
            credential.ticket_received_at = received_at

    matched_auths: list[PlatformAccountAuth] = []
    if authorizer_appid:
        matched_auths = list(
            (
                await session.scalars(
                    select(PlatformAccountAuth).where(
                        PlatformAccountAuth.org_id.in_(integration_org_ids),
                        PlatformAccountAuth.platform
                        == Platform.WECHAT_OFFICIAL_ACCOUNT.value,
                        PlatformAccountAuth.external_open_id == authorizer_appid,
                    )
                )
            ).all()
        )
    if info_type == "unauthorized":
        for auth in matched_auths:
            auth.auth_status = "unauthorized"
            auth.access_token_encrypted = None
            auth.refresh_token_encrypted = None
            auth.token_expires_at = None
            auth.refresh_expires_at = None
            auth.last_error = "WeChat authorization revoked"
            account = await session.get(Account, auth.account_id)
            if (
                account is not None
                and account.org_id == auth.org_id
                and account.platform == Platform.WECHAT_OFFICIAL_ACCOUNT
            ):
                account.auth = {
                    **(account.auth or {}),
                    "integration_status": "disconnected",
                    "auth_status": "unauthorized",
                }

    event_org_id = matched_auths[0].org_id if len(matched_auths) == 1 else None
    normalized_payload: dict[str, object] = {
        "component_appid": component_appid,
        "create_time": create_time,
        "info_type": info_type,
    }
    if authorizer_appid:
        normalized_payload["authorizer_appid"] = authorizer_appid
    session.add(
        Event(
            type=f"wechat.{info_type}",
            org_id=event_org_id,
            idempotency_key=fingerprint,
            payload=normalized_payload,
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if not await session.scalar(
            select(Event.id).where(Event.idempotency_key == fingerprint)
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WeChat callback could not be persisted",
            ) from exc
    return PlainTextResponse("success")


async def _read_bounded_wechat_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid WeChat callback",
            ) from exc
        if declared_length < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid WeChat callback",
            )
        if declared_length > WECHAT_CALLBACK_MAX_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="WeChat callback is too large",
            )
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > WECHAT_CALLBACK_MAX_BODY_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="WeChat callback is too large",
            )
        body.extend(chunk)
    return bytes(body)


@router.post("/platform-integrations/wechat/events")
async def handle_wechat_encrypted_event(
    request: Request,
    session: SessionDep,
    msg_signature: Annotated[str, Query(min_length=1, max_length=128)],
    timestamp: Annotated[str, Query(min_length=1, max_length=20)],
    nonce: Annotated[str, Query(min_length=1, max_length=128)],
) -> PlainTextResponse:
    verify_token, aes_key = _wechat_callback_config()
    body = await _read_bounded_wechat_body(request)
    if not timestamp.isdigit() or not 1 <= len(nonce) <= 128:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    callback_timestamp = int(timestamp)
    if abs(int(datetime.now(UTC).timestamp()) - callback_timestamp) > (
        WECHAT_CALLBACK_TIMESTAMP_WINDOW_SECONDS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    outer = _parse_wechat_xml(body)
    encrypted = _wechat_xml_text(outer, "Encrypt", max_length=65_536)
    if len(msg_signature) != 40 or any(
        char not in "0123456789abcdefABCDEF" for char in msg_signature
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    expected = hashlib.sha1(
        "".join(sorted((verify_token, timestamp, nonce, encrypted))).encode("utf-8")
    ).hexdigest()
    if not compare_digest(expected, msg_signature.lower()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    message, component_appid = _decrypt_wechat_callback(encrypted, aes_key)
    outer_appid = _wechat_xml_text(outer, "AppId", max_length=128)
    if not compare_digest(outer_appid, component_appid):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    integrations = await _wechat_integrations_for_appid(session, component_appid)
    inner = _parse_wechat_xml(message)
    inner_appid = inner.findtext("AppId")
    if inner_appid and not compare_digest(inner_appid.strip(), component_appid):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid WeChat callback",
        )
    return await _apply_wechat_callback_event(
        session, integrations=integrations, root=inner
    )


@router.get("/platform-integrations/{platform}", response_model=PlatformIntegrationOut)
async def get_platform_integration(
    platform: Annotated[Platform, Path()], admin: AdminUser, session: SessionDep
) -> PlatformIntegrationOut:
    row = await session.scalar(
        select(PlatformIntegration).where(
            PlatformIntegration.org_id == admin.org_id,
            PlatformIntegration.platform == platform.value,
        )
    )
    return _to_out(row) if row else _default_out(platform)


@router.patch("/platform-integrations/{platform}", response_model=PlatformIntegrationOut)
async def upsert_platform_integration(
    platform: Annotated[Platform, Path()],
    body: UpsertPlatformIntegrationRequest,
    admin: AdminUser,
    session: SessionDep,
) -> PlatformIntegrationOut:
    row = await session.scalar(
        select(PlatformIntegration).where(
            PlatformIntegration.org_id == admin.org_id,
            PlatformIntegration.platform == platform.value,
        )
    )
    if row is None:
        row = PlatformIntegration(
            org_id=admin.org_id,
            platform=platform.value,
            capabilities=_default_capabilities(platform),
            official_docs=_official_docs(platform),
            client_key=_default_client_key(platform),
            client_secret_ref=_default_client_secret_ref(platform),
        )
        session.add(row)

    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is not None:
            setattr(row, key, value)

    if not row.official_docs:
        row.official_docs = _official_docs(platform)
    if not row.capabilities:
        row.capabilities = _default_capabilities(platform)
    if platform == Platform.DOUYIN and not row.client_secret_ref:
        row.client_secret_ref = DEFAULT_DOUYIN_SECRET_REF

    _apply_platform_status_defaults(row)

    if row.status in {"configured", "pending_review", "connected"} and not row.client_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="client_key is required before enabling platform integration",
        )

    session.add(
        Event(
            type="platform.integration.updated",
            payload={
                "platform": platform.value,
                "status": row.status,
                "auth_status": row.auth_status,
                "data_sync_status": row.data_sync_status,
                "client_key_configured": bool(row.client_key),
                "client_secret_configured": row.client_secret_configured,
                "updated_by": admin.id,
            },
        )
    )
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.post(
    "/platform-integrations/douyin/oauth/authorize",
    response_model=DouyinAuthorizeOut,
)
async def create_douyin_oauth_authorize_url(
    body: DouyinAuthorizeRequest,
    admin: AdminUser,
    session: SessionDep,
) -> DouyinAuthorizeOut:
    integration = await _get_integration_or_404(session, admin.org_id, Platform.DOUYIN)
    _require_douyin_app(integration)
    account = await _get_owned_douyin_account(session, body.account_id, admin.org_id)
    redirect_uri = integration.redirect_uri
    if not redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="redirect_uri is required",
        )
    # Initial account binding requests only identity. Additional capabilities
    # use the explicit incremental authorization endpoint below.
    scopes = DEFAULT_DOUYIN_SCOPES
    state = _create_oauth_state(org_id=admin.org_id, account_id=account.id)
    return DouyinAuthorizeOut(
        platform=Platform.DOUYIN,
        client_key=integration.client_key or "",
        redirect_uri=redirect_uri,
        scopes=scopes,
        state=state,
        authorization_url=build_douyin_authorization_url(
            client_key=integration.client_key or "",
            redirect_uri=redirect_uri,
            scopes=scopes,
            state=state,
        ),
    )


@router.post(
    "/platform-integrations/douyin/oauth/scan-add",
    response_model=DouyinAuthorizeOut,
)
async def create_douyin_scan_add_authorize_url(
    body: DouyinScanAddRequest,
    admin: AdminUser,
    session: SessionDep,
) -> DouyinAuthorizeOut:
    integration = await _get_integration_or_404(session, admin.org_id, Platform.DOUYIN)
    _require_douyin_app(integration)
    await _validate_group(session, body.group_id, admin.org_id)
    await _validate_project(session, body.project_id, admin.org_id)
    redirect_uri = integration.redirect_uri
    if not redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="redirect_uri is required",
        )
    scopes = DEFAULT_DOUYIN_SCOPES
    state = _create_oauth_state(
        org_id=admin.org_id,
        account_id=None,
        flow="scan_add",
        nickname=body.nickname,
        group_id=body.group_id,
        project_id=body.project_id,
        initiated_by=admin.id,
    )
    return DouyinAuthorizeOut(
        platform=Platform.DOUYIN,
        client_key=integration.client_key or "",
        redirect_uri=redirect_uri,
        scopes=scopes,
        state=state,
        authorization_url=build_douyin_authorization_url(
            client_key=integration.client_key or "",
            redirect_uri=redirect_uri,
            scopes=scopes,
            state=state,
        ),
    )


@router.get(
    "/platform-integrations/douyin/accounts/{account_id}/capabilities",
    response_model=DouyinAccountCapabilitiesOut,
)
async def get_douyin_account_capabilities(
    account_id: int,
    admin: AdminUser,
    session: SessionDep,
) -> DouyinAccountCapabilitiesOut:
    await _get_owned_douyin_account(session, account_id, admin.org_id)
    integration = await _get_integration_or_404(session, admin.org_id, Platform.DOUYIN)
    auth = await _get_douyin_account_auth_or_conflict(session, account_id, admin.org_id)
    capabilities = diagnose_douyin_capabilities(
        app_scopes=integration.scopes or [],
        account_scopes=auth.scopes or [],
    )
    next_recommended = next(
        (str(item["key"]) for item in capabilities if item["status"] != "ready"),
        None,
    )
    return DouyinAccountCapabilitiesOut(
        account_id=account_id,
        configured_app_scopes=integration.scopes or [],
        granted_account_scopes=auth.scopes or [],
        capabilities=capabilities,
        next_recommended=next_recommended,
    )


@router.post(
    "/platform-integrations/douyin/oauth/incremental-authorize",
    response_model=DouyinAuthorizeOut,
)
async def create_douyin_incremental_authorize_url(
    body: DouyinIncrementalAuthorizeRequest,
    admin: AdminUser,
    session: SessionDep,
) -> DouyinAuthorizeOut:
    account = await _get_owned_douyin_account(session, body.account_id, admin.org_id)
    integration = await _get_integration_or_404(session, admin.org_id, Platform.DOUYIN)
    _require_douyin_app(integration)
    auth = await _get_douyin_account_auth_or_conflict(
        session, account.id, admin.org_id
    )
    capability = DOUYIN_CAPABILITY_BY_KEY[body.capability_key]
    configured = set(integration.scopes or [])
    missing_app = [scope for scope in capability.app_scopes if scope not in configured]
    if missing_app:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "请先在抖音开放平台申请并登记该能力",
                "capability_key": capability.key,
                "missing_app_scopes": missing_app,
            },
        )
    granted = set(auth.scopes or [])
    scopes = [scope for scope in capability.user_scopes if scope not in granted]
    if not scopes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "该能力不需要补充账号授权或已经完成授权",
                "capability_key": capability.key,
                "missing_app_scopes": [],
            },
        )
    if len(scopes) > 3:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Douyin incremental authorization cannot request more than 3 scopes",
        )
    redirect_uri = integration.redirect_uri
    if not redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="redirect_uri is required",
        )
    state = _create_oauth_state(
        org_id=admin.org_id,
        account_id=account.id,
        flow="incremental",
        initiated_by=admin.id,
    )
    return DouyinAuthorizeOut(
        platform=Platform.DOUYIN,
        client_key=integration.client_key or "",
        redirect_uri=redirect_uri,
        scopes=scopes,
        state=state,
        authorization_url=build_douyin_authorization_url(
            client_key=integration.client_key or "",
            redirect_uri=redirect_uri,
            scopes=scopes,
            state=state,
        ),
    )


@router.post(
    "/platform-integrations/douyin/oauth/trial-whitelist",
    response_model=DouyinTrialWhitelistOut,
)
async def create_douyin_trial_whitelist_authorize_url(
    admin: AdminUser,
    session: SessionDep,
) -> DouyinTrialWhitelistOut:
    integration = await _get_integration_or_404(session, admin.org_id, Platform.DOUYIN)
    _require_douyin_app(integration)
    redirect_uri = integration.redirect_uri
    if not redirect_uri:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="redirect_uri is required",
        )
    state = _create_oauth_state(
        org_id=admin.org_id,
        account_id=None,
        flow="trial_whitelist",
        initiated_by=admin.id,
    )
    authorization_url = build_douyin_authorization_url(
        client_key=integration.client_key or "",
        redirect_uri=redirect_uri,
        scopes=DOUYIN_TRIAL_WHITELIST_SCOPES,
        state=state,
    )
    return DouyinTrialWhitelistOut(
        platform=Platform.DOUYIN,
        client_key=integration.client_key or "",
        redirect_uri=redirect_uri,
        scopes=DOUYIN_TRIAL_WHITELIST_SCOPES,
        authorization_url=authorization_url,
    )


@router.get(
    "/platform-integrations/douyin/oauth/callback",
    response_model=DouyinOAuthCallbackOut,
)
async def handle_douyin_oauth_callback(
    session: SessionDep,
    code: Annotated[str, Query(min_length=1)],
    state: Annotated[str | None, Query()] = None,
    scopes: Annotated[str | None, Query()] = None,
) -> DouyinOAuthCallbackOut | HTMLResponse:
    if not state:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="state is required for account authorization",
        )
    payload = _decode_oauth_state(state)
    if payload.get("flow") == "trial_whitelist":
        await _complete_douyin_trial_whitelist(
            session=session,
            code=code,
            payload=payload,
        )
        return _trial_whitelist_completed_page()
    await _complete_douyin_oauth(session=session, code=code, state=state)
    return _account_authorization_completed_page(flow=str(payload.get("flow") or ""))


def _trial_whitelist_completed_page() -> HTMLResponse:
    return HTMLResponse(
        content="""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>测试白名单已完成</title></head>
<body style="margin:0;font-family:system-ui,sans-serif;background:#f7f7f7;color:#111">
<main style="max-width:520px;margin:15vh auto;padding:32px;background:#fff;
border:1px solid #ddd;border-radius:18px">
<h1 style="font-size:24px;margin:0 0 12px">测试白名单已完成</h1>
<p style="line-height:1.7;margin:0">
这个步骤只添加测试资格，不会创建账号。现在可以关闭页面，回到同舟行的“账号矩阵”，
点击“添加账号”进行正式扫码授权。
</p>
</main></body></html>""",
        status_code=status.HTTP_200_OK,
    )


def _account_authorization_completed_page(*, flow: str) -> HTMLResponse:
    title = "抖音账号添加成功" if flow == "scan_add" else "抖音账号授权成功"
    return HTMLResponse(
        content=f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;font-family:system-ui,sans-serif;background:#f7f7f7;color:#111">
<main style="max-width:520px;margin:15vh auto;padding:32px;background:#fff;
border:1px solid #ddd;border-radius:18px">
<h1 style="font-size:24px;margin:0 0 12px">{title}</h1>
<p style="line-height:1.7;margin:0 0 24px">
账号已完成官方授权并进入账号矩阵。现在可以关闭这个页面，返回同舟行继续操作。
</p>
<a href="/accounts" style="display:inline-block;padding:12px 18px;border-radius:12px;
background:#111;color:#fff;text-decoration:none">返回账号矩阵</a>
</main></body></html>""",
        status_code=status.HTTP_200_OK,
    )


async def _complete_douyin_trial_whitelist(
    *,
    session: AsyncSession,
    code: str,
    payload: dict,
) -> None:
    org_id = int(payload["org_id"])
    integration = await _get_integration_or_404(session, org_id, Platform.DOUYIN)
    _require_douyin_app(integration)
    try:
        token_data = await exchange_douyin_access_token(
            client_key=integration.client_key or "",
            client_secret=_resolve_client_secret(integration),
            code=code,
        )
    except DouyinIntegrationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    external_open_id = str(token_data.get("open_id") or token_data.get("openid") or "")
    granted_scopes = _parse_scopes(token_data.get("scope"))
    missing_scopes = set(DOUYIN_TRIAL_WHITELIST_SCOPES) - set(granted_scopes)
    if not external_open_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Douyin response missing open_id",
        )
    if missing_scopes:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Douyin response missing scopes: {','.join(sorted(missing_scopes))}",
        )

    session.add(
        Event(
            type="platform.douyin.trial_whitelist.authorized",
            payload={
                "platform": Platform.DOUYIN.value,
                "org_id": org_id,
                "external_open_id": external_open_id,
                "scopes": granted_scopes,
                "initiated_by": payload.get("initiated_by"),
            },
        )
    )
    await session.commit()


@router.post(
    "/platform-integrations/douyin/oauth/complete",
    response_model=DouyinOAuthCallbackOut,
)
async def complete_douyin_oauth_from_worker(
    body: DouyinOAuthCompleteRequest,
    session: SessionDep,
    worker_secret: Annotated[str | None, Header(alias="X-Dyflow-Worker-Secret")] = None,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> DouyinOAuthCallbackOut:
    _require_worker_secret(worker_secret, authorization)
    code, state = _extract_oauth_completion_params(body)
    return await _complete_douyin_oauth(session=session, code=code, state=state)


def _extract_oauth_completion_params(body: DouyinOAuthCompleteRequest) -> tuple[str, str]:
    code = body.code
    state = body.state
    if body.callback_url:
        query = parse_qs(urlparse(body.callback_url).query)
        code = code or (query.get("code") or [""])[0]
        state = state or (query.get("state") or [""])[0]
    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="code and state are required",
        )
    return code, state


async def _complete_douyin_oauth(
    *,
    session: AsyncSession,
    code: str,
    state: str,
) -> DouyinOAuthCallbackOut:
    payload = _decode_oauth_state(state)
    org_id = int(payload["org_id"])
    account_id = int(payload["account_id"]) if payload.get("account_id") is not None else None
    flow = str(payload.get("flow") or "bind_existing")
    integration = await _get_integration_or_404(session, org_id, Platform.DOUYIN)
    _require_douyin_app(integration)
    client_secret = _resolve_client_secret(integration)
    try:
        token_data = await exchange_douyin_access_token(
            client_key=integration.client_key or "",
            client_secret=client_secret,
            code=code,
        )
    except DouyinIntegrationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    external_open_id = str(token_data.get("open_id") or token_data.get("openid") or "")
    if not external_open_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Douyin response missing open_id",
        )
    scopes = _parse_scopes(token_data.get("scope")) or integration.scopes or DEFAULT_DOUYIN_SCOPES
    access_token = str(token_data.get("access_token") or "")
    refresh_token = str(token_data.get("refresh_token") or "")
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Douyin response missing access_token",
        )
    if flow == "scan_add":
        await _validate_group(session, payload.get("group_id"), org_id)
        await _validate_project(session, payload.get("project_id"), org_id)
        account = await _get_or_create_douyin_scan_account(
            session=session,
            org_id=org_id,
            external_open_id=external_open_id,
            nickname=payload.get("nickname") or token_data.get("nickname"),
            group_id=payload.get("group_id"),
            project_id=payload.get("project_id"),
            scopes=scopes,
        )
    else:
        if account_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="account_id is required"
            )
        account = await _get_owned_douyin_account(session, account_id, org_id)
        account.external_account_id = external_open_id
        _apply_authorized_account_meta(account, scopes)

    auth = await _upsert_platform_account_auth(
        session=session,
        org_id=org_id,
        account=account,
        external_open_id=external_open_id,
        union_id=token_data.get("union_id"),
        scopes=scopes,
        expires_in=int(token_data.get("expires_in") or 0),
        refresh_expires_in=int(token_data.get("refresh_expires_in") or 0),
        access_token=access_token,
        refresh_token=refresh_token or None,
    )
    if "user_info" in scopes:
        try:
            profile = await fetch_douyin_user_info(
                access_token=access_token,
                open_id=external_open_id,
            )
        except DouyinIntegrationError as exc:
            auth.last_error = f"Profile sync pending: {exc}"
        else:
            normalized_profile = normalize_douyin_user_profile(profile)
            auth.raw_profile = normalized_profile["raw_profile"]
            auth.union_id = normalized_profile.get("union_id") or auth.union_id
            if normalized_profile.get("nickname"):
                account.nickname = str(normalized_profile["nickname"])
            account.auth = {
                **(account.auth or {}),
                "avatar": normalized_profile.get("avatar"),
            }
    session.add(
        Event(
            type="platform.douyin.oauth.authorized",
            payload={
                "platform": Platform.DOUYIN.value,
                "account_id": account.id,
                "external_open_id": external_open_id,
                "scopes": auth.scopes,
                "flow": flow,
            },
        )
    )
    await session.commit()
    await session.refresh(auth)
    return DouyinOAuthCallbackOut(
        account_id=auth.account_id,
        platform=Platform.DOUYIN,
        external_open_id=auth.external_open_id or external_open_id,
        union_id=auth.union_id,
        auth_status=auth.auth_status,
        data_sync_status=auth.data_sync_status,
        scopes=auth.scopes or [],
        token_configured=auth.token_configured,
    )


async def _upsert_platform_account_auth(
    *,
    session: AsyncSession,
    org_id: int,
    account: Account,
    external_open_id: str,
    union_id: object,
    scopes: list[str],
    expires_in: int,
    refresh_expires_in: int,
    access_token: str,
    refresh_token: str | None,
) -> PlatformAccountAuth:
    row = None
    row = await session.scalar(
        select(PlatformAccountAuth).where(PlatformAccountAuth.account_id == account.id)
    )
    if row is None:
        row = PlatformAccountAuth(
            org_id=org_id,
            account_id=account.id,
            platform=Platform.DOUYIN.value,
        )
        session.add(row)
    now = datetime.now(UTC)
    row.external_open_id = external_open_id
    row.union_id = str(union_id) if union_id else None
    row.auth_status = "authorized"
    row.data_sync_status = "pending"
    row.scopes = sorted(set(row.scopes or []) | set(scopes))
    _apply_authorized_account_meta(account, row.scopes)
    try:
        row.access_token_encrypted = encrypt_credential(access_token)
        row.refresh_token_encrypted = (
            encrypt_credential(refresh_token) if refresh_token else None
        )
    except CredentialEncryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    row.token_secret_ref = None
    row.refresh_secret_ref = None
    row.token_expires_at = now + timedelta(seconds=expires_in) if expires_in > 0 else None
    row.refresh_expires_at = (
        now + timedelta(seconds=refresh_expires_in) if refresh_expires_in > 0 else None
    )
    row.raw_profile = {"open_id": external_open_id, "union_id": row.union_id}
    return row


@router.post(
    "/platform-integrations/douyin/js-signature",
    response_model=DouyinJsSignatureOut,
)
async def create_douyin_js_signature(
    body: DouyinJsSignatureRequest,
    user: CurrentUser,
    session: SessionDep,
) -> DouyinJsSignatureOut:
    integration = await _get_integration_or_404(session, user.org_id, Platform.DOUYIN)
    _require_douyin_app(integration)
    client_secret = _resolve_client_secret(integration)
    try:
        ticket = await get_douyin_jsb_ticket(
            integration=integration,
            client_secret=client_secret,
        )
    except DouyinIntegrationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    signature = create_js_signature(ticket=ticket, url=body.url)
    return DouyinJsSignatureOut(
        platform=Platform.DOUYIN,
        client_key=integration.client_key or "",
        nonce_str=signature["nonce_str"],
        timestamp=signature["timestamp"],
        url=signature["url"],
        signature=signature["signature"],
    )


@router.post(
    "/platform-integrations/douyin/accounts/{account_id}/sync-metrics",
    response_model=DouyinDataSyncOut,
)
async def sync_douyin_account_metrics(
    account_id: int,
    admin: AdminUser,
    session: SessionDep,
) -> DouyinDataSyncOut:
    account = await _get_owned_douyin_account(session, account_id, admin.org_id)
    auth = await _get_douyin_account_auth_or_conflict(session, account.id, admin.org_id)
    if auth.auth_status != "authorized" or not auth.external_open_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Douyin account is not authorized",
        )
    try:
        access_token = await _resolve_douyin_sync_access_token(
            session=session,
            auth=auth,
            org_id=admin.org_id,
        )
    except (CredentialEncryptionError, SecretNotConfiguredError, DouyinIntegrationError) as exc:
        auth.data_sync_status = "failed"
        auth.last_error = str(exc)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    auth.data_sync_status = "syncing"
    auth.last_error = None
    try:
        profile = await fetch_douyin_user_info(
            access_token=access_token,
            open_id=auth.external_open_id,
        )
    except DouyinIntegrationError as exc:
        auth.data_sync_status = "failed"
        auth.last_error = str(exc)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    normalized_profile = normalize_douyin_user_profile(profile)
    auth.raw_profile = normalized_profile["raw_profile"]
    auth.union_id = normalized_profile.get("union_id") or auth.union_id
    account.external_account_id = auth.external_open_id
    if normalized_profile.get("nickname"):
        account.nickname = str(normalized_profile["nickname"])
    account.auth = {
        **(account.auth or {}),
        "avatar": normalized_profile.get("avatar"),
        "integration_status": "connected",
        "auth_status": "authorized",
        "data_sync_status": "pending",
        "metrics_sync_mode": "posting_task_required",
        "metrics_sync_note": "抖音作品列表接口已下线，作品数据需通过投稿任务回收。",
    }

    now = datetime.now(UTC)
    auth.data_sync_status = "pending"
    auth.last_sync_at = now
    session.add(
        Event(
            type="platform.douyin.profile.synced",
            payload={
                "platform": Platform.DOUYIN.value,
                "account_id": account.id,
                "video_count": 0,
                "snapshot_count": 0,
                "metrics_sync_mode": "posting_task_required",
            },
        )
    )
    await session.commit()
    await session.refresh(auth)
    return DouyinDataSyncOut(
        account_id=account.id,
        platform=Platform.DOUYIN,
        data_sync_status=auth.data_sync_status,
        profile_synced=bool(profile),
        video_count=0,
        snapshot_count=0,
        last_sync_at=auth.last_sync_at or now,
    )


async def _resolve_douyin_sync_access_token(
    *,
    session: AsyncSession,
    auth: PlatformAccountAuth,
    org_id: int,
) -> str:
    if auth.access_token_encrypted:
        access_token = decrypt_credential(auth.access_token_encrypted)
    else:
        access_token = resolve_douyin_account_token_ref(auth.token_secret_ref)

    if not _credential_expires_soon(auth.token_expires_at):
        return access_token
    if not auth.refresh_token_encrypted:
        raise SecretNotConfiguredError("Douyin refresh token is not configured")
    if auth.refresh_expires_at and _credential_expires_soon(auth.refresh_expires_at, seconds=0):
        auth.auth_status = "expired"
        raise SecretNotConfiguredError("Douyin refresh token has expired; authorize again")

    integration = await _get_integration_or_404(session, org_id, Platform.DOUYIN)
    _require_douyin_app(integration)
    refresh_token = decrypt_credential(auth.refresh_token_encrypted)
    token_data = await refresh_douyin_access_token(
        client_key=integration.client_key or "",
        refresh_token=refresh_token,
    )
    new_access_token = str(token_data.get("access_token") or "")
    if not new_access_token:
        raise DouyinIntegrationError("Douyin response missing access_token")
    new_refresh_token = str(token_data.get("refresh_token") or refresh_token)
    now = datetime.now(UTC)
    expires_in = int(token_data.get("expires_in") or 0)
    refresh_expires_in = int(token_data.get("refresh_expires_in") or 0)
    auth.access_token_encrypted = encrypt_credential(new_access_token)
    auth.refresh_token_encrypted = encrypt_credential(new_refresh_token)
    auth.token_expires_at = now + timedelta(seconds=expires_in) if expires_in > 0 else None
    if refresh_expires_in > 0:
        auth.refresh_expires_at = now + timedelta(seconds=refresh_expires_in)
    auth.auth_status = "authorized"
    return new_access_token


def _credential_expires_soon(value: datetime | None, *, seconds: int = 300) -> bool:
    if value is None:
        return False
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return normalized <= datetime.now(UTC) + timedelta(seconds=seconds)


async def _get_douyin_account_auth_or_conflict(
    session: AsyncSession, account_id: int, org_id: int
) -> PlatformAccountAuth:
    auth = await session.scalar(
        select(PlatformAccountAuth).where(
            PlatformAccountAuth.account_id == account_id,
            PlatformAccountAuth.org_id == org_id,
            PlatformAccountAuth.platform == Platform.DOUYIN.value,
        )
    )
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Douyin account authorization is not configured",
        )
    return auth
