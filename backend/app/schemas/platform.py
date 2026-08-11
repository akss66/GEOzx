"""Schemas for official platform integrations."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Platform

PlatformIntegrationStatus = Literal[
    "not_configured", "configured", "pending_review", "connected", "disabled"
]
PlatformAuthStatus = Literal["not_configured", "unauthorized", "authorized", "expired", "manual"]
PlatformDataSyncStatus = Literal[
    "not_configured", "pending", "syncing", "healthy", "failed", "manual"
]


class UpsertPlatformIntegrationRequest(BaseModel):
    status: PlatformIntegrationStatus | None = None
    client_key: str | None = Field(default=None, max_length=128)
    client_secret_ref: str | None = Field(default=None, max_length=256)
    redirect_uri: str | None = Field(default=None, max_length=500)
    js_sdk_domain: str | None = Field(default=None, max_length=500)
    auth_status: PlatformAuthStatus | None = None
    data_sync_status: PlatformDataSyncStatus | None = None
    scopes: list[str] | None = None
    capabilities: dict[str, str] | None = None
    note: str | None = Field(default=None, max_length=1000)


class PlatformIntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None
    platform: Platform
    status: str
    client_key: str | None
    client_secret_configured: bool
    redirect_uri: str | None
    js_sdk_domain: str | None
    auth_status: str
    data_sync_status: str
    scopes: list[str]
    capabilities: dict[str, str]
    official_docs: list[str]
    note: str | None
    created_at: datetime | None
    updated_at: datetime | None


class PlatformAccountAuthOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    platform: Platform
    external_open_id: str | None
    union_id: str | None
    auth_status: str
    data_sync_status: str
    scopes: list[str]
    token_configured: bool
    token_expires_at: datetime | None
    refresh_expires_at: datetime | None
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class WechatAuthorizationGrant(BaseModel):
    authorizer_appid: str
    authorizer_access_token: str | None
    authorizer_refresh_token: str | None
    expires_in: int | None
    func_info: list[int]


class WechatAuthorizationSessionRequest(BaseModel):
    client_id: int | None = Field(default=None, gt=0)
    project_id: int | None = Field(default=None, gt=0)
    knowledge_base_id: int | None = Field(default=None, gt=0)


class WechatAuthorizationSessionOut(BaseModel):
    authorization_url: str
    expires_at: datetime
    state_id: str


class WechatPreAuthCodeResponse(BaseModel):
    pre_auth_code: str = Field(min_length=1, max_length=512)
    expires_in: int = Field(gt=0)


class DouyinAuthorizeRequest(BaseModel):
    account_id: int


class DouyinIncrementalAuthorizeRequest(BaseModel):
    account_id: int
    capability_key: Literal["profile", "h5_publish", "posting_feedback"]


class DouyinScanAddRequest(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=200)
    group_id: int | None = None
    project_id: int | None = None


class DouyinOAuthCompleteRequest(BaseModel):
    callback_url: str | None = Field(default=None, max_length=2000)
    code: str | None = Field(default=None, min_length=1, max_length=1000)
    state: str | None = Field(default=None, min_length=1, max_length=2000)


class DouyinAuthorizeOut(BaseModel):
    platform: Platform
    client_key: str
    redirect_uri: str
    scopes: list[str]
    state: str
    authorization_url: str


class DouyinTrialWhitelistOut(BaseModel):
    platform: Platform
    client_key: str
    redirect_uri: str
    scopes: list[str]
    authorization_url: str


class DouyinOAuthCallbackOut(BaseModel):
    account_id: int | None
    platform: Platform
    external_open_id: str
    union_id: str | None
    auth_status: str
    data_sync_status: str
    scopes: list[str]
    token_configured: bool


class DouyinJsSignatureRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1000)


class DouyinJsSignatureOut(BaseModel):
    platform: Platform
    client_key: str
    nonce_str: str
    timestamp: int
    url: str
    signature: str


class DouyinDataSyncOut(BaseModel):
    account_id: int
    platform: Platform
    data_sync_status: str
    profile_synced: bool
    video_count: int
    snapshot_count: int
    last_sync_at: datetime


class DouyinCapabilityStatusOut(BaseModel):
    key: str
    label: str
    description: str
    app_scopes: list[str]
    user_scopes: list[str]
    missing_app_scopes: list[str]
    missing_user_scopes: list[str]
    status: Literal[
        "ready", "needs_app_permission", "needs_account_authorization"
    ]


class DouyinAccountCapabilitiesOut(BaseModel):
    account_id: int
    platform: Literal["douyin"] = "douyin"
    configured_app_scopes: list[str]
    granted_account_scopes: list[str]
    capabilities: list[DouyinCapabilityStatusOut]
    next_recommended: str | None
