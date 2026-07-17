"""User management routes restricted to organization administrators."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser
from app.core.security import hash_password
from app.db import get_session
from app.models import Event, User
from app.schemas.auth import (
    CreateUserRequest,
    UpdateUserAccessRequest,
    UpdateUserRequest,
    UserAccessCatalogOut,
    UserDetailOut,
    UserOut,
)
from app.services.user_management import (
    build_user_detail,
    get_access_catalog,
    get_org_user,
    replace_user_access,
    update_org_user,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[UserOut]:
    rows = await session.scalars(
        select(User).where(User.org_id == admin.org_id).order_by(User.is_active.desc(), User.id)
    )
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
    await session.flush()
    session.add(
        Event(
            type="user.created",
            payload={
                "org_id": admin.org_id,
                "actor_user_id": admin.id,
                "target_user_id": user.id,
                "email": user.email,
                "role": user.role.value,
            },
        )
    )
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.get("/access-catalog", response_model=UserAccessCatalogOut)
async def read_access_catalog(
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserAccessCatalogOut:
    return await get_access_catalog(session, admin.org_id)


@router.get("/{user_id}", response_model=UserDetailOut)
async def get_user_detail(
    user_id: int,
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserDetailOut:
    user = await get_org_user(session, org_id=admin.org_id, user_id=user_id)
    return await build_user_detail(session, user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UpdateUserRequest,
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserOut:
    user = await get_org_user(session, org_id=admin.org_id, user_id=user_id)
    updated = await update_org_user(session, actor=admin, target=user, body=body)
    return UserOut.model_validate(updated)


@router.put("/{user_id}/access", response_model=UserDetailOut)
async def update_user_access(
    user_id: int,
    body: UpdateUserAccessRequest,
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserDetailOut:
    user = await get_org_user(session, org_id=admin.org_id, user_id=user_id)
    return await replace_user_access(session, actor=admin, target=user, body=body)
