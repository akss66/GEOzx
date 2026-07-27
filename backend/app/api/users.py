"""User management routes restricted to organization administrators."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser
from app.core.security import hash_password
from app.db import get_session
from app.models import Event, User
from app.schemas.auth import (
    CreateUserRequest,
    PermanentDeleteUserOut,
    PermanentDeleteUserRequest,
    ResetUserPasswordRequest,
    SecondaryPasswordStatusOut,
    SetSecondaryPasswordRequest,
    UpdateUserAccessRequest,
    UpdateUserRequest,
    UserAccessCatalogOut,
    UserDeletionImpactOut,
    UserDetailOut,
    UserOut,
)
from app.services.admin_security import get_secondary_password_status, set_secondary_password
from app.services.user_deletion import (
    build_deletion_impact,
    execute_permanent_deletion,
    issue_deletion_preview_token,
)
from app.services.user_management import (
    build_user_detail,
    get_access_catalog,
    get_org_user,
    replace_user_access,
    reset_org_user_password,
    update_org_user,
)

router = APIRouter(prefix="/users", tags=["users"])


def _secondary_password_status(
    credential, deletion_available: bool
) -> SecondaryPasswordStatusOut:
    return SecondaryPasswordStatusOut(
        configured=credential is not None,
        deletion_available=deletion_available,
        delete_available_at=credential.delete_available_at if credential else None,
        locked_until=credential.locked_until if credential else None,
    )


@router.put("/me/secondary-password", response_model=SecondaryPasswordStatusOut)
async def update_secondary_password(
    body: SetSecondaryPasswordRequest,
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SecondaryPasswordStatusOut:
    credential = await set_secondary_password(
        session, admin, body.current_password, body.secondary_password
    )
    return _secondary_password_status(credential, deletion_available=False)


@router.get("/me/secondary-password/status", response_model=SecondaryPasswordStatusOut)
async def read_secondary_password_status(
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SecondaryPasswordStatusOut:
    credential, deletion_available = await get_secondary_password_status(session, admin)
    return _secondary_password_status(credential, deletion_available)


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


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: int,
    body: ResetUserPasswordRequest,
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    user = await get_org_user(session, org_id=admin.org_id, user_id=user_id)
    await reset_org_user_password(
        session,
        actor=admin,
        target=user,
        new_password=body.new_password,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/deletion-preview", response_model=UserDeletionImpactOut)
async def preview_user_deletion(
    user_id: int,
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserDeletionImpactOut:
    user = await get_org_user(session, org_id=admin.org_id, user_id=user_id)
    operation_id = uuid4().hex
    session.add(
        Event(
            type="user.deletion_previewed",
            payload={
                "actor_user_id": admin.id,
                "target_user_id": user.id,
                "operation_id": operation_id,
            },
        )
    )
    await session.flush()
    impact = await build_deletion_impact(session, actor=admin, target=user)
    preview_token, expires_at = issue_deletion_preview_token(
        actor=admin,
        target=user,
        operation_id=operation_id,
        impact=impact,
    )
    await session.commit()
    return UserDeletionImpactOut(
        target_user_id=user.id,
        target_email=user.email,
        counts=impact.counts,
        preview_token=preview_token,
        expires_at=expires_at,
        allowed=not impact.blockers,
        blockers=list(impact.blockers),
    )


@router.delete("/{user_id}/permanent", response_model=PermanentDeleteUserOut)
async def permanently_delete_user(
    user_id: int,
    body: PermanentDeleteUserRequest,
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PermanentDeleteUserOut:
    receipt = await execute_permanent_deletion(
        session,
        actor=admin,
        target_user_id=user_id,
        preview_token=body.preview_token,
        secondary_password=body.secondary_password,
    )
    return PermanentDeleteUserOut(
        operation_id=receipt.operation_id,
        deleted_at=receipt.deleted_at,
        counts=receipt.counts,
    )


@router.put("/{user_id}/access", response_model=UserDetailOut)
async def update_user_access(
    user_id: int,
    body: UpdateUserAccessRequest,
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserDetailOut:
    user = await get_org_user(session, org_id=admin.org_id, user_id=user_id)
    return await replace_user_access(session, actor=admin, target=user, body=body)
