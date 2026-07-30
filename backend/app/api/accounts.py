"""账号路由：账号矩阵 + 分组 CRUD。

账号与分组属系统配置/矩阵管理职责，增删改限 admin；list/get 按客户与项目授权过滤。
所有操作同时按当前用户 org 隔离。
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import AdminUser, CurrentUser
from app.core.outbound_url import OutboundRequestError, UnsafeOutboundURLError
from app.core.workspace_access import (
    accessible_account_clause,
    require_account_access,
    require_client_access,
    require_project_access,
)
from app.core.workspace_defaults import get_or_create_default_client
from app.db import get_session
from app.models import (
    Account,
    AccountClient,
    AccountGroup,
    BrainTask,
    Client,
    ContentItem,
    Event,
    PlatformAccountAuth,
    Project,
    ProjectAccount,
    User,
)
from app.models.enums import (
    AccountStatus,
    BrainTaskStatus,
    DeliverableAcceptanceStatus,
    DeliverableType,
    UserRole,
    WorkspaceRole,
)
from app.schemas.ai_coo import AccountSituationOut
from app.schemas.workspace import (
    AccountGroupOut,
    AccountMatrixGroupOut,
    AccountMatrixOut,
    AccountOut,
    BatchUpdateAccountsRequest,
    CreateAccountGroupRequest,
    CreateAccountRequest,
    CreateDistributionActionRequest,
    DistributionActionOut,
    PlatformMatrixSummaryOut,
    ReplaceAccountAssignmentsRequest,
    UpdateAccountIntegrationRequest,
    UpdateAccountRequest,
    account_out,
)
from app.services.account_avatar import (
    UnsupportedAccountAvatarError,
    fetch_account_avatar,
)
from app.services.ai_coo_evidence import build_account_situation

router = APIRouter(tags=["accounts"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _resolve_avatar_url(
    account: Account,
    platform_auth: PlatformAccountAuth | None,
) -> str | None:
    raw_profile = (
        platform_auth.raw_profile
        if platform_auth is not None and isinstance(platform_auth.raw_profile, dict)
        else {}
    )
    account_auth = account.auth if isinstance(account.auth, dict) else {}
    value = (
        raw_profile.get("avatar")
        or raw_profile.get("avatar_url")
        or account_auth.get("avatar")
        or account_auth.get("avatar_url")
    )
    return value if isinstance(value, str) and value else None


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
    session: AsyncSession, content_item_id: int | None, user: User
) -> None:
    if content_item_id is None:
        return
    item = await session.scalar(
        select(ContentItem)
        .join(Project, ContentItem.project_id == Project.id)
        .where(ContentItem.id == content_item_id, Project.org_id == user.org_id)
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="内容不存在")
    if item.project_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="内容不存在")
    await require_project_access(session, user, item.project_id)


async def _project_ids_by_account(
    session: AsyncSession, account_ids: list[int]
) -> dict[int, list[int]]:
    if not account_ids:
        return {}
    rows = await session.execute(
        select(ProjectAccount.account_id, ProjectAccount.project_id).where(
            ProjectAccount.account_id.in_(account_ids)
        )
    )
    result: dict[int, list[int]] = {}
    for account_id, project_id in rows:
        result.setdefault(account_id, []).append(project_id)
    return result


async def _client_ids_by_account(
    session: AsyncSession, account_ids: list[int]
) -> dict[int, list[int]]:
    if not account_ids:
        return {}
    rows = await session.execute(
        select(AccountClient.account_id, AccountClient.client_id).where(
            AccountClient.account_id.in_(account_ids)
        )
    )
    result: dict[int, list[int]] = {}
    for account_id, client_id in rows:
        result.setdefault(account_id, []).append(client_id)
    return result


async def _account_operational_context(
    session: AsyncSession,
    accounts: Sequence[Account],
    org_id: int,
) -> dict[int, dict]:
    """Assemble the real operational state shown by every account-matrix view."""
    if not accounts:
        return {}

    account_by_id = {account.id: account for account in accounts}
    account_ids = set(account_by_id)
    auth_rows = (
        await session.scalars(
            select(PlatformAccountAuth).where(
                PlatformAccountAuth.org_id == org_id,
                PlatformAccountAuth.account_id.in_(account_ids),
            )
        )
    ).all()
    auth_by_account = {row.account_id: row for row in auth_rows}

    tasks = (
        await session.scalars(
            select(BrainTask)
            .options(selectinload(BrainTask.brief), selectinload(BrainTask.acceptances))
            .where(BrainTask.org_id == org_id)
            .order_by(BrainTask.updated_at.desc(), BrainTask.id.desc())
        )
    ).all()

    result: dict[int, dict] = {}
    for account_id, account in account_by_id.items():
        auth = auth_by_account.get(account_id)
        auth_status = auth.auth_status if auth is not None else account.auth_status
        if auth_status == "authorized":
            publish_capability = "prepare_only"
        elif auth_status == "manual" or account.integration_status == "manual":
            publish_capability = "manual_only"
        else:
            publish_capability = "unavailable"

        result[account_id] = {
            "avatar_url": _resolve_avatar_url(account, auth),
            "positioning_summary": None,
            "current_task": None,
            "risk_count": 0,
            "last_sync_at": auth.last_sync_at if auth is not None else None,
            "publish_capability": publish_capability,
        }

    active_statuses = {
        BrainTaskStatus.DRAFT,
        BrainTaskStatus.PENDING_CONFIRMATION,
        BrainTaskStatus.RUNNING,
        BrainTaskStatus.PENDING_ACCEPTANCE,
    }
    for task in tasks:
        brief_account_ids = set(task.brief.account_ids if task.brief is not None else [])
        scoped_account_ids = account_ids.intersection(brief_account_ids)
        if not scoped_account_ids:
            continue

        approved_positioning = next(
            (
                acceptance
                for acceptance in sorted(
                    task.acceptances,
                    key=lambda item: (item.updated_at, item.id),
                    reverse=True,
                )
                if acceptance.deliverable_type == DeliverableType.POSITIONING_STRATEGY
                and acceptance.status == DeliverableAcceptanceStatus.APPROVED
                and acceptance.summary
            ),
            None,
        )
        for account_id in scoped_account_ids:
            context = result[account_id]
            if context["positioning_summary"] is None and approved_positioning is not None:
                context["positioning_summary"] = approved_positioning.summary
            if task.status in active_statuses:
                if context["current_task"] is None:
                    context["current_task"] = {
                        "id": task.id,
                        "title": task.title,
                        "status": task.status.value,
                        "progress": task.progress,
                        "current_focus": task.current_focus,
                    }
                    context["risk_count"] = task.risk_count

    return result


async def _account_response(session: AsyncSession, account: Account) -> AccountOut:
    project_ids = await _project_ids_by_account(session, [account.id])
    client_ids = await _client_ids_by_account(session, [account.id])
    operational = await _account_operational_context(session, [account], account.org_id)
    return account_out(
        account,
        project_ids.get(account.id),
        operational.get(account.id),
        client_ids.get(account.id),
    )


async def _load_distribution_accounts(
    session: AsyncSession,
    account_ids: list[int],
    user: User,
) -> list[Account]:
    unique_ids = sorted(set(account_ids))
    accessible_accounts = await accessible_account_clause(session, user)
    accounts = list(
        await session.scalars(
            select(Account).where(
                Account.org_id == user.org_id,
                Account.id.in_(unique_ids),
                accessible_accounts,
            )
        )
    )
    if len(accounts) != len(unique_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    if any(account.status != AccountStatus.ACTIVE for account in accounts):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="账号不可用于分发")
    for account in accounts:
        await require_account_access(
            session,
            user,
            account.id,
            roles={WorkspaceRole.LEAD, WorkspaceRole.OPERATOR},
        )
    return accounts


@router.get("/account-groups", response_model=list[AccountGroupOut])
async def list_account_groups(user: CurrentUser, session: SessionDep) -> list[AccountGroupOut]:
    q = select(AccountGroup).where(AccountGroup.org_id == user.org_id)
    accessible_accounts = await accessible_account_clause(session, user)
    if user.role != UserRole.ADMIN:
        visible_group_ids = select(Account.group_id).where(
            accessible_accounts,
            Account.group_id.is_not(None),
        )
        q = q.where(AccountGroup.id.in_(visible_group_ids))
    rows = await session.scalars(q.order_by(AccountGroup.id))
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
    accessible_accounts = await accessible_account_clause(session, user)
    q = (
        select(Account)
        .where(Account.org_id == user.org_id, accessible_accounts)
        .order_by(Account.id)
    )
    if group_id is not None:
        q = q.where(Account.group_id == group_id)
    if project_id is not None:
        await require_project_access(session, user, project_id)
        linked_accounts = select(ProjectAccount.account_id).where(
            ProjectAccount.project_id == project_id
        )
        q = q.where(or_(Account.project_id == project_id, Account.id.in_(linked_accounts)))
    rows = (await session.scalars(q)).all()
    project_ids = await _project_ids_by_account(session, [row.id for row in rows])
    client_ids = await _client_ids_by_account(session, [row.id for row in rows])
    operational = await _account_operational_context(session, rows, user.org_id)
    return [
        account_out(
            row,
            project_ids.get(row.id),
            operational.get(row.id),
            client_ids.get(row.id),
        )
        for row in rows
    ]


@router.get("/account-matrix", response_model=AccountMatrixOut)
async def get_account_matrix(
    user: CurrentUser,
    session: SessionDep,
    project_id: Annotated[int | None, Query()] = None,
) -> AccountMatrixOut:
    accessible_accounts = await accessible_account_clause(session, user)
    visible_group_ids = select(Account.group_id).where(
        accessible_accounts,
        Account.group_id.is_not(None),
    )
    groups = (
        await session.scalars(
            select(AccountGroup)
            .where(
                AccountGroup.org_id == user.org_id,
                AccountGroup.id.in_(visible_group_ids),
            )
            .order_by(AccountGroup.id)
        )
    ).all()
    q = (
        select(Account)
        .where(Account.org_id == user.org_id, accessible_accounts)
        .order_by(Account.id)
    )
    if project_id is not None:
        await require_project_access(session, user, project_id)
        linked_accounts = select(ProjectAccount.account_id).where(
            ProjectAccount.project_id == project_id
        )
        q = q.where(or_(Account.project_id == project_id, Account.id.in_(linked_accounts)))
    accounts = (await session.scalars(q)).all()
    project_ids = await _project_ids_by_account(session, [row.id for row in accounts])
    client_ids = await _client_ids_by_account(session, [row.id for row in accounts])
    operational = await _account_operational_context(session, accounts, user.org_id)

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
                    account_out(
                        row,
                        project_ids.get(row.id),
                        operational.get(row.id),
                        client_ids.get(row.id),
                    )
                    for row in accounts_by_group.get(group.id, [])
                ],
            )
            for group in groups
        ],
        ungrouped_accounts=[
            account_out(
                row,
                project_ids.get(row.id),
                operational.get(row.id),
                client_ids.get(row.id),
            )
            for row in accounts_by_group.get(None, [])
        ],
        platforms=platform_rows,
    )


@router.post("/accounts", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: CreateAccountRequest, admin: AdminUser, session: SessionDep
) -> AccountOut:
    await _validate_group(session, body.group_id, admin.org_id)
    await _validate_project(session, body.project_id, admin.org_id)
    client = (
        await require_client_access(session, admin, body.client_id)
        if body.client_id is not None
        else await get_or_create_default_client(session, admin.org_id)
    )
    if body.project_id is not None:
        project = await session.get(Project, body.project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目不存在",
            )
        if project.client_id != client.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="项目不属于当前客户",
            )
    account = Account(
        org_id=admin.org_id,
        client_id=client.id,
        nickname=body.nickname,
        platform=body.platform,
        group_id=body.group_id,
        project_id=body.project_id,
        external_account_id=body.external_account_id,
    )
    session.add(account)
    await session.flush()
    session.add(AccountClient(client_id=client.id, account_id=account.id))
    if body.project_id is not None:
        session.add(ProjectAccount(project_id=body.project_id, account_id=account.id))
    await session.commit()
    await session.refresh(account)
    return await _account_response(session, account)


@router.patch("/accounts/batch", response_model=list[AccountOut])
async def batch_update_accounts(
    body: BatchUpdateAccountsRequest,
    admin: AdminUser,
    session: SessionDep,
) -> list[AccountOut]:
    account_ids = sorted(set(body.account_ids))
    accounts = (
        await session.scalars(
            select(Account)
            .where(Account.org_id == admin.org_id, Account.id.in_(account_ids))
            .order_by(Account.id)
        )
    ).all()
    if len(accounts) != len(account_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")

    fields = body.model_fields_set
    if "group_id" in fields:
        await _validate_group(session, body.group_id, admin.org_id)
    project = None
    if "project_id" in fields:
        await _validate_project(session, body.project_id, admin.org_id)
        if body.project_id is not None:
            project = await session.get(Project, body.project_id)
            wrong_client = project is not None and any(
                project.client_id != account.client_id for account in accounts
            )
            if project is None or wrong_client:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="项目不属于所选账号的当前客户",
                )

    existing_links: set[tuple[int, int]] = set()
    if project is not None:
        rows = await session.execute(
            select(ProjectAccount.account_id, ProjectAccount.project_id).where(
                ProjectAccount.account_id.in_(account_ids),
                ProjectAccount.project_id == project.id,
            )
        )
        existing_links = set(rows.tuples())

    for account in accounts:
        if "group_id" in fields:
            account.group_id = body.group_id
        if "project_id" in fields:
            account.project_id = body.project_id
            if project is not None and (account.id, project.id) not in existing_links:
                session.add(ProjectAccount(project_id=project.id, account_id=account.id))
        if "status" in fields and body.status is not None:
            account.status = body.status

    event_payload = body.model_dump(exclude={"account_ids"}, exclude_unset=True)
    session.add(
        Event(
            type="accounts.batch_updated",
            payload={
                "account_ids": account_ids,
                "updated_by": admin.id,
                **{
                    key: value.value if hasattr(value, "value") else value
                    for key, value in event_payload.items()
                },
            },
        )
    )
    await session.commit()

    project_ids = await _project_ids_by_account(session, account_ids)
    client_ids = await _client_ids_by_account(session, account_ids)
    operational = await _account_operational_context(session, accounts, admin.org_id)
    return [
        account_out(
            account,
            project_ids.get(account.id),
            operational.get(account.id),
            client_ids.get(account.id),
        )
        for account in accounts
    ]


@router.get("/accounts/{account_id}", response_model=AccountOut)
async def get_account(
    account_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> AccountOut:
    account = await require_account_access(session, user, account_id)
    return await _account_response(session, account)


@router.get("/accounts/{account_id}/avatar")
async def get_account_avatar(
    account_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    account = await require_account_access(session, user, account_id)
    platform_auth = await session.scalar(
        select(PlatformAccountAuth).where(
            PlatformAccountAuth.org_id == user.org_id,
            PlatformAccountAuth.account_id == account.id,
        )
    )
    avatar_url = _resolve_avatar_url(account, platform_auth)
    if avatar_url is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="账号头像尚未同步",
        )
    try:
        image = await fetch_account_avatar(avatar_url)
    except (
        OutboundRequestError,
        UnsafeOutboundURLError,
        UnsupportedAccountAvatarError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="账号头像暂时不可用",
        ) from exc
    return Response(
        content=image.content,
        media_type=image.content_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/accounts/{account_id}/situation", response_model=AccountSituationOut)
async def get_account_situation(
    account_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> AccountSituationOut:
    account = await require_account_access(session, user, account_id)
    return await build_account_situation(
        session,
        org_id=account.org_id,
        account_id=account.id,
    )


@router.put("/accounts/{account_id}/assignments", response_model=AccountOut)
async def replace_account_assignments(
    account_id: int,
    body: ReplaceAccountAssignmentsRequest,
    admin: AdminUser,
    session: SessionDep,
) -> AccountOut:
    account = await _get_owned_account(session, account_id, admin.org_id)
    clients: list[Client] = []
    if body.client_ids:
        clients = list(
            await session.scalars(
                select(Client).where(
                    Client.org_id == admin.org_id,
                    Client.id.in_(body.client_ids),
                )
            )
        )
        if len(clients) != len(body.client_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="客户不存在")

    projects: list[Project] = []
    if body.project_ids:
        projects = list(
            await session.scalars(
                select(Project).where(
                    Project.org_id == admin.org_id,
                    Project.id.in_(body.project_ids),
                )
            )
        )
        if len(projects) != len(body.project_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    client_id_set = set(body.client_ids)
    if any(project.client_id not in client_id_set for project in projects):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="所选项目必须属于已绑定客户",
        )
    if body.default_project_id is not None and body.default_client_id is not None:
        default_project = next(
            project for project in projects if project.id == body.default_project_id
        )
        if default_project.client_id != body.default_client_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="默认项目必须属于默认客户",
            )

    await session.execute(
        delete(AccountClient).where(AccountClient.account_id == account.id)
    )
    await session.execute(
        delete(ProjectAccount).where(ProjectAccount.account_id == account.id)
    )
    session.add_all(
        [
            AccountClient(account_id=account.id, client_id=client_id)
            for client_id in body.client_ids
        ]
    )
    session.add_all(
        [
            ProjectAccount(account_id=account.id, project_id=project_id)
            for project_id in body.project_ids
        ]
    )
    account.client_id = body.default_client_id
    account.project_id = body.default_project_id
    session.add(
        Event(
            type="account.assignments_replaced",
            payload={
                "account_id": account.id,
                "client_ids": sorted(body.client_ids),
                "project_ids": sorted(body.project_ids),
                "default_client_id": body.default_client_id,
                "default_project_id": body.default_project_id,
                "updated_by": admin.id,
            },
        )
    )
    await session.commit()
    await session.refresh(account)
    return await _account_response(session, account)


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
        if data["project_id"] is not None:
            project = await session.get(Project, data["project_id"])
            if project is None or project.client_id != account.client_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="项目不属于当前客户",
                )
            existing = await session.scalar(
                select(ProjectAccount).where(
                    ProjectAccount.project_id == project.id,
                    ProjectAccount.account_id == account.id,
                )
            )
            if existing is None:
                session.add(ProjectAccount(project_id=project.id, account_id=account.id))
    for key, value in data.items():
        setattr(account, key, value)
    await session.commit()
    await session.refresh(account)
    return await _account_response(session, account)


@router.patch("/accounts/{account_id}/integration", response_model=AccountOut)
async def update_account_integration(
    account_id: int,
    body: UpdateAccountIntegrationRequest,
    admin: AdminUser,
    session: SessionDep,
) -> AccountOut:
    account = await _get_owned_account(session, account_id, admin.org_id)
    data = body.model_dump(exclude_unset=True)
    note = data.pop("note", None)
    manual_status = {
        "integration_status": "manual",
        "auth_status": "manual",
        "data_sync_status": "manual",
    }
    if data != manual_status:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="官方授权和同步状态只能由平台回调或同步任务更新",
        )
    if (
        account.integration_status not in {"oauth_ready", "manual"}
        or account.auth_status not in {"unauthorized", "manual"}
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已存在官方接入状态，不能切换为开发模式",
        )

    meta = dict(account.auth or {})
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
    return await _account_response(session, account)


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
    if body.project_id is not None:
        await require_project_access(session, user, body.project_id)
    await _validate_content_item(session, body.content_item_id, user)
    accounts = await _load_distribution_accounts(session, body.account_ids, user)
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
