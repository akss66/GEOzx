"""Authentication and user-management schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.models.enums import ClientStatus, ProjectStatus, UserRole, WorkspaceRole


class LoginRequest(BaseModel):
    # 登录按字符串查库即可，不对格式做严格校验（避免拒绝合法的已存标识，如 .local 域名）
    email: str
    password: str


class SetSecondaryPasswordRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    secondary_password: str = Field(min_length=8, max_length=128)


class SecondaryPasswordStatusOut(BaseModel):
    configured: bool
    deletion_available: bool
    delete_available_at: datetime | None
    locked_until: datetime | None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    role: UserRole
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)
    role: UserRole = UserRole.USER


class UpdateUserRequest(BaseModel):
    email: EmailStr | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("至少提交一个修改字段")
        return self


class ClientMembershipOut(BaseModel):
    client_id: int
    client_name: str
    role: WorkspaceRole


class ProjectMembershipOut(BaseModel):
    project_id: int
    project_name: str
    client_id: int | None
    client_name: str | None
    role: WorkspaceRole


class UserDetailOut(UserOut):
    has_global_access: bool
    client_memberships: list[ClientMembershipOut]
    project_memberships: list[ProjectMembershipOut]


class ClientAccessCatalogItem(BaseModel):
    id: int
    name: str
    status: ClientStatus


class ProjectAccessCatalogItem(BaseModel):
    id: int
    client_id: int | None
    name: str
    status: ProjectStatus


class UserAccessCatalogOut(BaseModel):
    clients: list[ClientAccessCatalogItem]
    projects: list[ProjectAccessCatalogItem]


class ClientAccessInput(BaseModel):
    client_id: int
    role: WorkspaceRole


class ProjectAccessInput(BaseModel):
    project_id: int
    role: WorkspaceRole


class UpdateUserAccessRequest(BaseModel):
    clients: list[ClientAccessInput] = Field(default_factory=list, max_length=500)
    projects: list[ProjectAccessInput] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def reject_duplicate_resources(self):
        client_ids = [item.client_id for item in self.clients]
        project_ids = [item.project_id for item in self.projects]
        if len(client_ids) != len(set(client_ids)):
            raise ValueError("客户授权不能重复")
        if len(project_ids) != len(set(project_ids)):
            raise ValueError("项目授权不能重复")
        return self
