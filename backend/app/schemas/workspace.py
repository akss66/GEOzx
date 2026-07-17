"""工作区域 schema：项目 / 账号分组 / 账号（矩阵）的请求与响应。"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import AccountStatus, GroupDimension, Platform, ProjectStatus

# —— 项目 ——


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    client_id: int | None = None
    monthly_cost_budget_usd: Decimal | None = Field(default=None, ge=0, max_digits=12)


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: ProjectStatus | None = None
    monthly_cost_budget_usd: Decimal | None = Field(default=None, ge=0, max_digits=12)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int | None
    name: str
    description: str | None
    monthly_cost_budget_usd: Decimal | None
    status: ProjectStatus
    created_at: datetime


# —— 账号分组 ——


class CreateAccountGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dimension: GroupDimension = GroupDimension.TRACK


class AccountGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    dimension: GroupDimension
    created_at: datetime


# —— 账号 ——


class CreateAccountRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=200)
    platform: Platform
    client_id: int | None = None
    group_id: int | None = None
    project_id: int | None = None
    external_account_id: str | None = Field(default=None, max_length=128)


class UpdateAccountRequest(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=200)
    group_id: int | None = None
    project_id: int | None = None
    status: AccountStatus | None = None
    external_account_id: str | None = Field(default=None, max_length=128)


class BatchUpdateAccountsRequest(BaseModel):
    account_ids: list[int] = Field(min_length=1, max_length=200)
    group_id: int | None = None
    project_id: int | None = None
    status: AccountStatus | None = None

    @model_validator(mode="after")
    def require_update(self):
        if not self.model_fields_set.intersection({"group_id", "project_id", "status"}):
            raise ValueError("至少提供一个批量更新字段")
        if len(set(self.account_ids)) != len(self.account_ids):
            raise ValueError("账号列表不能包含重复项")
        return self


IntegrationStatus = Literal["oauth_ready", "connected", "manual", "disabled"]
AuthStatus = Literal["unauthorized", "authorized", "expired", "manual"]
DataSyncStatus = Literal["not_configured", "pending", "syncing", "healthy", "failed", "manual"]


class UpdateAccountIntegrationRequest(BaseModel):
    integration_status: IntegrationStatus | None = None
    auth_status: AuthStatus | None = None
    data_sync_status: DataSyncStatus | None = None
    note: str | None = Field(default=None, max_length=500)


class AccountCurrentTaskOut(BaseModel):
    id: int
    title: str
    status: str
    progress: int
    current_focus: str


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int | None
    nickname: str
    platform: Platform
    group_id: int | None
    project_id: int | None
    project_ids: list[int] = Field(default_factory=list)
    status: AccountStatus
    external_account_id: str | None
    integration_status: str
    auth_status: str
    data_sync_status: str
    avatar_url: str | None = None
    positioning_summary: str | None = None
    current_task: AccountCurrentTaskOut | None = None
    risk_count: int = 0
    last_sync_at: datetime | None = None
    publish_capability: Literal["prepare_only", "manual_only", "unavailable"] = "unavailable"
    created_at: datetime


class AccountMatrixGroupOut(BaseModel):
    id: int
    name: str
    dimension: GroupDimension
    accounts: list[AccountOut]


class PlatformMatrixSummaryOut(BaseModel):
    platform: Platform
    total: int
    active: int
    integration_status: str
    auth_status: str
    data_sync_status: str


class AccountMatrixOut(BaseModel):
    groups: list[AccountMatrixGroupOut]
    ungrouped_accounts: list[AccountOut]
    platforms: list[PlatformMatrixSummaryOut]


def account_out(
    account,
    project_ids: list[int] | None = None,
    operational: dict | None = None,
) -> AccountOut:
    data = {
        "id": account.id,
        "client_id": account.client_id,
        "nickname": account.nickname,
        "platform": account.platform,
        "group_id": account.group_id,
        "project_id": account.project_id,
        "project_ids": sorted(
            project_ids or ([] if account.project_id is None else [account.project_id])
        ),
        "status": account.status,
        "external_account_id": account.external_account_id,
        "integration_status": account.integration_status,
        "auth_status": account.auth_status,
        "data_sync_status": account.data_sync_status,
        "created_at": account.created_at,
        **(operational or {}),
    }
    return AccountOut.model_validate(data)


class CreateDistributionActionRequest(BaseModel):
    platform: Platform
    account_ids: list[int] = Field(min_length=1)
    action_type: str = Field(default="manual_publish", min_length=1, max_length=80)
    content_item_id: int | None = None
    project_id: int | None = None
    note: str | None = Field(default=None, max_length=1000)


class DistributionActionOut(BaseModel):
    id: int
    platform: Platform
    account_ids: list[int]
    action_type: str
    status: str
    content_item_id: int | None
    project_id: int | None
    note: str | None
    created_at: datetime
