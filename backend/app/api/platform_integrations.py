"""Official platform integration configuration APIs."""

from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import Annotated
from urllib.parse import parse_qs, urlparse

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
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
from app.models import (
    Account,
    AccountGroup,
    Event,
    PlatformAccountAuth,
    PlatformIntegration,
    Project,
)
from app.models.enums import Platform
from app.schemas.platform import (
    DouyinAuthorizeOut,
    DouyinAuthorizeRequest,
    DouyinDataSyncOut,
    DouyinJsSignatureOut,
    DouyinJsSignatureRequest,
    DouyinOAuthCallbackOut,
    DouyinOAuthCompleteRequest,
    DouyinScanAddRequest,
    DouyinTrialWhitelistOut,
    PlatformIntegrationOut,
    UpsertPlatformIntegrationRequest,
)

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
    scopes = integration.scopes or DEFAULT_DOUYIN_SCOPES
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
    scopes = integration.scopes or DEFAULT_DOUYIN_SCOPES
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
    row.scopes = scopes
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
