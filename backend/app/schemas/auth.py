"""认证相关 Pydantic schema。"""

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.enums import UserRole


class LoginRequest(BaseModel):
    # 登录按字符串查库即可，不对格式做严格校验（避免拒绝合法的已存标识，如 .local 域名）
    email: str
    password: str


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
    password: str
    display_name: str
    role: UserRole = UserRole.USER
