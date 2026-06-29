"""工作区域 schema：项目 / 账号分组 / 账号（矩阵）的请求与响应。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AccountStatus, GroupDimension, Platform, ProjectStatus

# —— 项目 ——


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: ProjectStatus | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
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
    group_id: int | None = None
    external_account_id: str | None = Field(default=None, max_length=128)


class UpdateAccountRequest(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=200)
    group_id: int | None = None
    status: AccountStatus | None = None
    external_account_id: str | None = Field(default=None, max_length=128)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nickname: str
    platform: Platform
    group_id: int | None
    status: AccountStatus
    external_account_id: str | None
    created_at: datetime
