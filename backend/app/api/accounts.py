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
from app.models import Account, AccountGroup, ContentItem, Event, PlatformAccountAuth, Project
from app.models.enums import AccountStatus
from app.schemas.workspace import (
    AccountGroupOut,
    AccountMatrixGroupOut,
    AccountMatrixOut,
    AccountOut,
    CreateAccountGroupRequest,
    CreateAccountRequest,
    CreateDistributionActionRequest,
    DistributionActionOut,
    PlatformMatrixSummaryOut,
    UpdateAccountIntegrationRequest,
    UpdateAccountRequest,
)

router = APIRouter(tags=["accounts"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _get_owned_account(session: AsyncSession, account_id: int, org_id: int) -> Account:
    account = await session.get(Account, account_id)
    if account is None or account.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    return account


async def _sync_platform_account_auth(
    session: AsyncSession,
    account: Account,
    auth_status: str,
    data_sync_status: str,
) -> PlatformAccountAuth:
    row = await session.scalar(
        select(PlatformAccountAuth).where(PlatformAccountAuth.account_id == account.id)
    )
    if row is None:
        row = PlatformAccountAuth(
            org_id=account.org_id,
            account_id=account.id,
            platform=account.platform.value,
        )
        session.add(row)
    row.external_open_id = account.external_account_id
    row.auth_status = auth_status
    row.data_sync_status = data_sync_status
    return row


async def _validate_group(session: AsyncSession, group_id: int | None, org_id: int) -> None:
    """校验分组归属当前 org（防跨 org 引用）。"""
    if group_id is None:
        return
    group = await session.get(AccountGroup, group_id)
    if group is None or group.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分组不存在")


# —— 账号分组 ——


def _rollup_status(values: list[str], preferred: list[str], empty: str) -> str:
    if not values:
        return empty
    for value in preferred:
        if value in values:
            return value
    return values[0]


async def _validate_project(session: AsyncSession, project_id: int | None, org_id: int) -> None:
    if project_id is None:
        return
    project = await session.get(Project, project_id)
    if project is None or project.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")


async def _validate_content_item(
    session: AsyncSession, content_item_id: int | None, org_id: int
) -> None:
    if content_item_id is None:
        return
    item = await session.scalar(
        select(ContentItem)
        .join(Project, ContentItem.project_id == Project.id)
        .where(ContentItem.id == content_item_id, Project.org_id == org_id)
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="内容不存在")


async def _load_distribution_accounts(
    session: AsyncSession,
    account_ids: list[int],
    org_id: int,
) -> list[Account]:
    unique_ids = sorted(set(account_ids))
    accounts = (
        await session.scalars(
            select(Account).where(Account.org_id == org_id, Account.id.in_(unique_ids))
        )
    ).all()
    if len(accounts) != len(unique_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    if any(account.status != AccountStatus.ACTIVE for account in accounts):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="账号不可用于分发")
    return accounts


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
    project_id: Annotated[int | None, Query()] = None,
) -> list[AccountOut]:
    q = select(Account).where(Account.org_id == user.org_id).order_by(Account.id)
    if group_id is not None:
        q = q.where(Account.group_id == group_id)
    if project_id is not None:
        await _validate_project(session, project_id, user.org_id)
        q = q.where(Account.project_id == project_id)
    rows = await session.scalars(q)
    return [AccountOut.model_validate(a) for a in rows]


@router.get("/account-matrix", response_model=AccountMatrixOut)
async def get_account_matrix(
    user: CurrentUser,
    session: SessionDep,
    project_id: Annotated[int | None, Query()] = None,
) -> AccountMatrixOut:
    groups = (
        await session.scalars(
            select(AccountGroup).where(AccountGroup.org_id == user.org_id).order_by(AccountGroup.id)
        )
    ).all()
    q = select(Account).where(Account.org_id == user.org_id).order_by(Account.id)
    if project_id is not None:
        await _validate_project(session, project_id, user.org_id)
        q = q.where(Account.project_id == project_id)
    accounts = (await session.scalars(q)).all()

    accounts_by_group: dict[int | None, list[Account]] = {}
    for account in accounts:
        accounts_by_group.setdefault(account.group_id, []).append(account)

    platform_rows: list[PlatformMatrixSummaryOut] = []
    for platform in sorted({account.platform for account in accounts}, key=lambda item: item.value):
        platform_accounts = [account for account in accounts if account.platform == platform]
        integration_status = _rollup_status(
            [account.integration_status for account in platform_accounts],
            ["connected", "oauth_ready", "manual", "disabled"],
            "manual",
        )
        auth_status = _rollup_status(
            [account.auth_status for account in platform_accounts],
            ["expired", "authorized", "manual", "unauthorized"],
            "unauthorized",
        )
        data_sync_status = _rollup_status(
            [account.data_sync_status for account in platform_accounts],
            ["failed", "syncing", "healthy", "pending", "manual", "not_configured"],
            "not_configured",
        )
        platform_rows.append(
            PlatformMatrixSummaryOut(
                platform=platform,
                total=len(platform_accounts),
                active=sum(1 for account in platform_accounts if account.status.value == "active"),
                integration_status=integration_status,
                auth_status=auth_status,
                data_sync_status=data_sync_status,
            )
        )

    return AccountMatrixOut(
        groups=[
            AccountMatrixGroupOut(
                id=group.id,
                name=group.name,
                dimension=group.dimension,
                accounts=[
                    AccountOut.model_validate(row)
                    for row in accounts_by_group.get(group.id, [])
                ],
            )
            for group in groups
        ],
        ungrouped_accounts=[
            AccountOut.model_validate(row) for row in accounts_by_group.get(None, [])
        ],
        platforms=platform_rows,
    )


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: CreateAccountRequest, admin: AdminUser, session: SessionDep
) -> AccountOut:
    await _validate_group(session, body.group_id, admin.org_id)
    await _validate_project(session, body.project_id, admin.org_id)
    account = Account(
        org_id=admin.org_id,
        nickname=body.nickname,
        platform=body.platform,
        group_id=body.group_id,
        project_id=body.project_id,
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
    if "project_id" in data:
        await _validate_project(session, data["project_id"], admin.org_id)
    for key, value in data.items():
        setattr(account, key, value)
    await session.commit()
    await session.refresh(account)
    return AccountOut.model_validate(account)


@router.patch("/accounts/{account_id}/integration", response_model=AccountOut)
async def update_account_integration(
    account_id: int,
    body: UpdateAccountIntegrationRequest,
    admin: AdminUser,
    session: SessionDep,
) -> AccountOut:
    account = await _get_owned_account(session, account_id, admin.org_id)
    meta = dict(account.auth or {})
    data = body.model_dump(exclude_unset=True)
    note = data.pop("note", None)
    meta.update({key: value for key, value in data.items() if value is not None})
    if note is not None:
        meta["note"] = note
    account.auth = meta
    await _sync_platform_account_auth(
        session,
        account,
        auth_status=account.auth_status,
        data_sync_status=account.data_sync_status,
    )
    session.add(
        Event(
            type="account.integration.updated",
            payload={
                "account_id": account.id,
                "platform": account.platform.value,
                "integration_status": account.integration_status,
                "auth_status": account.auth_status,
                "data_sync_status": account.data_sync_status,
                "updated_by": admin.id,
                "note": note,
            },
        )
    )
    await session.commit()
    await session.refresh(account)
    return AccountOut.model_validate(account)


@router.post(
    "/distribution/actions",
    response_model=DistributionActionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_distribution_action(
    body: CreateDistributionActionRequest,
    user: CurrentUser,
    session: SessionDep,
) -> DistributionActionOut:
    await _validate_project(session, body.project_id, user.org_id)
    await _validate_content_item(session, body.content_item_id, user.org_id)
    accounts = await _load_distribution_accounts(session, body.account_ids, user.org_id)
    if any(account.platform != body.platform for account in accounts):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="账号平台不匹配")

    payload = {
        "platform": body.platform.value,
        "account_ids": sorted(account.id for account in accounts),
        "action_type": body.action_type,
        "status": "recorded",
        "note": body.note,
        "created_by": user.id,
    }
    event = Event(
        type="distribution.action",
        content_item_id=body.content_item_id,
        project_id=body.project_id,
        payload=payload,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return DistributionActionOut(
        id=event.id,
        platform=body.platform,
        account_ids=payload["account_ids"],
        action_type=body.action_type,
        status=payload["status"],
        content_item_id=body.content_item_id,
        project_id=body.project_id,
        note=body.note,
        created_at=event.created_at,
    )


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: int, admin: AdminUser, session: SessionDep) -> None:
    account = await _get_owned_account(session, account_id, admin.org_id)
    await session.delete(account)
    await session.commit()
