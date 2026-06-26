"""用户管理路由（仅 admin）。体现"admin 负责用户管理"的角色边界。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser
from app.core.security import hash_password
from app.db import get_session
from app.models import User
from app.schemas.auth import CreateUserRequest, UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[UserOut]:
    rows = await session.scalars(select(User).where(User.org_id == admin.org_id))
    return [UserOut.model_validate(u) for u in rows]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserOut:
    exists = await session.scalar(select(User).where(User.email == body.email))
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已存在")
    user = User(
        org_id=admin.org_id,
        email=body.email,
        hashed_password=hash_password(body.password),
        display_name=body.display_name,
        role=body.role,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)
