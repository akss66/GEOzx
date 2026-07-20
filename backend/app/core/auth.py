"""鉴权与 RBAC 依赖。

- `get_current_user`：从 Bearer 令牌解析并加载用户（401）。
- `require_role(*roles)`：角色守卫依赖工厂（403）。
- 便捷别名：`CurrentUser`（任意已登录用户）、`AdminUser`（仅 admin）。
"""

from collections.abc import AsyncIterator
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import reset_acting_user, set_acting_user
from app.core.security import decode_token
from app.db import get_session
from app.models import User
from app.models.enums import UserRole

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AsyncIterator[User]:
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证凭证")
    try:
        payload = decode_token(creds.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效或过期的令牌"
        ) from exc

    sub = payload.get("sub")
    user = await session.get(User, int(sub)) if sub else None
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")
    context_token = set_acting_user(user.id)
    try:
        yield user
    finally:
        reset_acting_user(context_token)


def require_role(*roles: UserRole):
    """返回一个校验用户角色的依赖。"""

    async def _checker(
        user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return user

    return _checker


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_role(UserRole.ADMIN))]
