"""账号路由：账号矩阵 + 分组 CRUD。

账号与分组属系统配置/矩阵管理职责，增删改限 admin；list/get 任意登录用户可用。
均按当前用户 org 隔离。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser
from app.db import get_session
from app.models import Account, AccountGroup
from app.schemas.workspace import (
    AccountGroupOut,
    AccountOut,
    CreateAccountGroupRequest,
    CreateAccountRequest,
    UpdateAccountRequest,
)

router = APIRouter(tags=["accounts"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _get_owned_account(session: AsyncSession, account_id: int, org_id: int) -> Account:
    account = await session.get(Account, account_id)
    if account is None or account.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    return account


async def _validate_group(session: AsyncSession, group_id: int | None, org_id: int) -> None:
    """校验分组归属当前 org（防跨 org 引用）。"""
    if group_id is None:
        return
    group = await session.get(AccountGroup, group_id)
    if group is None or group.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分组不存在")


# —— 账号分组 ——


@router.get("/account-groups", response_model=list[AccountGroupOut])
async def list_account_groups(user: CurrentUser, session: SessionDep) -> list[AccountGroupOut]:
    rows = await session.scalars(
        select(AccountGroup).where(AccountGroup.org_id == user.org_id).order_by(AccountGroup.id)
    )
    return [AccountGroupOut.model_validate(g) for g in rows]


@router.post(
    "/account-groups", response_model=AccountGroupOut, status_code=status.HTTP_201_CREATED
)
async def create_account_group(
    body: CreateAccountGroupRequest, admin: AdminUser, session: SessionDep
) -> AccountGroupOut:
    group = AccountGroup(org_id=admin.org_id, name=body.name, dimension=body.dimension)
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return AccountGroupOut.model_validate(group)


# —— 账号 ——


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(
    user: CurrentUser,
    session: SessionDep,
    group_id: Annotated[int | None, Query()] = None,
) -> list[AccountOut]:
    q = select(Account).where(Account.org_id == user.org_id).order_by(Account.id)
    if group_id is not None:
        q = q.where(Account.group_id == group_id)
    rows = await session.scalars(q)
    return [AccountOut.model_validate(a) for a in rows]


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: CreateAccountRequest, admin: AdminUser, session: SessionDep
) -> AccountOut:
    await _validate_group(session, body.group_id, admin.org_id)
    account = Account(
        org_id=admin.org_id,
        nickname=body.nickname,
        platform=body.platform,
        group_id=body.group_id,
        external_account_id=body.external_account_id,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return AccountOut.model_validate(account)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: int, body: UpdateAccountRequest, admin: AdminUser, session: SessionDep
) -> AccountOut:
    account = await _get_owned_account(session, account_id, admin.org_id)
    data = body.model_dump(exclude_unset=True)
    if "group_id" in data:
        await _validate_group(session, data["group_id"], admin.org_id)
    for key, value in data.items():
        setattr(account, key, value)
    await session.commit()
    await session.refresh(account)
    return AccountOut.model_validate(account)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: int, admin: AdminUser, session: SessionDep) -> None:
    account = await _get_owned_account(session, account_id, admin.org_id)
    await session.delete(account)
    await session.commit()
