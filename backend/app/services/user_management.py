"""User lifecycle, workspace authorization, and audit helpers."""

from fastapi import HTTPException, status
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import (
    Account,
    AccountMembership,
    Client,
    ClientMembership,
    Event,
    Project,
    ProjectAccount,
    ProjectMembership,
    User,
)
from app.models.enums import UserRole
from app.schemas.auth import (
    AccountAccessCatalogItem,
    ClientAccessCatalogItem,
    ClientMembershipOut,
    ProjectAccessCatalogItem,
    ProjectMembershipOut,
    UpdateUserAccessRequest,
    UpdateUserRequest,
    UserAccessCatalogOut,
    UserDetailOut,
)


async def get_org_user(
    session: AsyncSession,
    *,
    org_id: int,
    user_id: int,
) -> User:
    user = await session.get(User, user_id)
    if user is None or user.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return user


async def build_user_detail(session: AsyncSession, user: User) -> UserDetailOut:
    client_rows = (
        await session.execute(
            select(ClientMembership, Client.name)
            .join(Client, Client.id == ClientMembership.client_id)
            .where(ClientMembership.user_id == user.id)
            .order_by(Client.name, Client.id)
        )
    ).all()
    project_rows = (
        await session.execute(
            select(ProjectMembership, Project, Client.name)
            .join(Project, Project.id == ProjectMembership.project_id)
            .outerjoin(Client, Client.id == Project.client_id)
            .where(ProjectMembership.user_id == user.id)
            .order_by(Project.name, Project.id)
        )
    ).all()
    account_ids = list(
        await session.scalars(
            select(AccountMembership.account_id)
            .join(Account, Account.id == AccountMembership.account_id)
            .where(
                AccountMembership.user_id == user.id,
                Account.org_id == user.org_id,
            )
            .order_by(AccountMembership.account_id)
        )
    )
    return UserDetailOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        has_global_access=user.role == UserRole.ADMIN,
        account_scope_mode=user.account_scope_mode,
        account_ids=account_ids,
        client_memberships=[
            ClientMembershipOut(
                client_id=membership.client_id,
                client_name=client_name,
                role=membership.role,
            )
            for membership, client_name in client_rows
        ],
        project_memberships=[
            ProjectMembershipOut(
                project_id=membership.project_id,
                project_name=project.name,
                client_id=project.client_id,
                client_name=client_name,
                role=membership.role,
            )
            for membership, project, client_name in project_rows
        ],
    )


async def get_access_catalog(session: AsyncSession, org_id: int) -> UserAccessCatalogOut:
    clients = list(
        await session.scalars(
            select(Client).where(Client.org_id == org_id).order_by(Client.name, Client.id)
        )
    )
    projects = list(
        await session.scalars(
            select(Project).where(Project.org_id == org_id).order_by(Project.name, Project.id)
        )
    )
    accounts = list(
        await session.scalars(
            select(Account).where(Account.org_id == org_id).order_by(Account.nickname, Account.id)
        )
    )
    project_rows = []
    if accounts:
        project_rows = (
            await session.execute(
                select(ProjectAccount.account_id, ProjectAccount.project_id).where(
                    ProjectAccount.account_id.in_([account.id for account in accounts])
                )
            )
        ).all()
    project_ids_by_account: dict[int, set[int]] = {}
    for account_id, project_id in project_rows:
        project_ids_by_account.setdefault(account_id, set()).add(project_id)
    return UserAccessCatalogOut(
        clients=[
            ClientAccessCatalogItem(id=item.id, name=item.name, status=item.status)
            for item in clients
        ],
        projects=[
            ProjectAccessCatalogItem(
                id=item.id,
                client_id=item.client_id,
                name=item.name,
                status=item.status,
            )
            for item in projects
        ],
        accounts=[
            AccountAccessCatalogItem(
                id=item.id,
                client_id=item.client_id,
                project_ids=sorted(
                    project_ids_by_account.get(item.id, set())
                    | ({item.project_id} if item.project_id is not None else set())
                ),
                nickname=item.nickname,
                platform=item.platform,
                status=item.status,
            )
            for item in accounts
        ],
    )


async def update_org_user(
    session: AsyncSession,
    *,
    actor: User,
    target: User,
    body: UpdateUserRequest,
) -> User:
    changes = body.model_dump(exclude_unset=True)
    requested_role = changes.get("role", target.role)
    requested_active = changes.get("is_active", target.is_active)
    removes_admin_access = target.role == UserRole.ADMIN and target.is_active and (
        requested_role != UserRole.ADMIN or requested_active is False
    )

    if target.id == actor.id and removes_admin_access:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="不能停用自己或移除自己的管理员权限",
        )
    if removes_admin_access:
        active_admin_ids = tuple(
            await session.scalars(
                select(User.id)
                .where(
                    User.org_id == actor.org_id,
                    User.role == UserRole.ADMIN,
                    User.is_active.is_(True),
                )
                .order_by(User.id)
                .with_for_update()
            )
        )
        if len(active_admin_ids) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="组织必须保留至少一个启用中的管理员",
            )

    if "email" in changes:
        normalized_email = str(changes["email"]).strip().lower()
        existing = await session.scalar(
            select(User).where(User.email == normalized_email, User.id != target.id)
        )
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邮箱已存在")
        changes["email"] = normalized_email

    before = {
        "email": target.email,
        "display_name": target.display_name,
        "role": target.role.value,
        "is_active": target.is_active,
    }
    for key, value in changes.items():
        setattr(target, key, value)
    session.add(
        Event(
            type="user.updated",
            payload={
                "org_id": actor.org_id,
                "actor_user_id": actor.id,
                "target_user_id": target.id,
                "before": before,
                "changes": {
                    key: value.value if hasattr(value, "value") else value
                    for key, value in changes.items()
                },
            },
        )
    )
    await session.commit()
    await session.refresh(target)
    return target


async def reset_org_user_password(
    session: AsyncSession,
    *,
    actor: User,
    target: User,
    new_password: str,
) -> None:
    if actor.id == target.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "USER_SELF_PASSWORD_RESET_FORBIDDEN",
                "message": "Administrators cannot reset their own password here",
            },
        )
    target.hashed_password = hash_password(new_password)
    session.add(
        Event(
            type="user.password_reset",
            payload={
                "org_id": actor.org_id,
                "actor_user_id": actor.id,
                "target_user_id": target.id,
            },
        )
    )
    await session.commit()


async def replace_user_access(
    session: AsyncSession,
    *,
    actor: User,
    target: User,
    body: UpdateUserAccessRequest,
) -> UserDetailOut:
    if target.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="管理员拥有全局访问权限，无需单独授权",
        )

    requested_client_ids = {item.client_id for item in body.clients}
    requested_project_ids = {item.project_id for item in body.projects}
    requested_account_ids = set(body.account_ids)
    if requested_client_ids:
        valid_client_ids = set(
            await session.scalars(
                select(Client.id).where(
                    Client.org_id == actor.org_id,
                    Client.id.in_(requested_client_ids),
                )
            )
        )
        if valid_client_ids != requested_client_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="客户不存在")
    if requested_project_ids:
        valid_project_ids = set(
            await session.scalars(
                select(Project.id).where(
                    Project.org_id == actor.org_id,
                    Project.id.in_(requested_project_ids),
                )
            )
        )
        if valid_project_ids != requested_project_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    if body.account_scope_mode == "selected" and requested_account_ids:
        valid_accounts = list(
            await session.scalars(
                select(Account).where(
                    Account.org_id == actor.org_id,
                    Account.id.in_(requested_account_ids),
                )
            )
        )
        if {account.id for account in valid_accounts} != requested_account_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")

        scope_clauses = []
        if requested_client_ids:
            scope_clauses.append(Account.client_id.in_(requested_client_ids))
        if requested_project_ids:
            linked_accounts = select(ProjectAccount.account_id).where(
                ProjectAccount.project_id.in_(requested_project_ids)
            )
            scope_clauses.extend(
                [
                    Account.project_id.in_(requested_project_ids),
                    Account.id.in_(linked_accounts),
                ]
            )
        scoped_account_ids = set()
        if scope_clauses:
            scoped_account_ids = set(
                await session.scalars(
                    select(Account.id).where(
                        Account.org_id == actor.org_id,
                        Account.id.in_(requested_account_ids),
                        or_(*scope_clauses),
                    )
                )
            )
        if scoped_account_ids != requested_account_ids:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")

    await session.execute(
        delete(ClientMembership).where(ClientMembership.user_id == target.id)
    )
    await session.execute(
        delete(ProjectMembership).where(ProjectMembership.user_id == target.id)
    )
    await session.execute(
        delete(AccountMembership).where(AccountMembership.user_id == target.id)
    )
    target.account_scope_mode = body.account_scope_mode
    memberships = [
        ClientMembership(
            client_id=item.client_id,
            user_id=target.id,
            role=item.role,
        )
        for item in body.clients
    ] + [
        ProjectMembership(
            project_id=item.project_id,
            user_id=target.id,
            role=item.role,
        )
        for item in body.projects
    ]
    if body.account_scope_mode == "selected":
        memberships.extend(
            AccountMembership(user_id=target.id, account_id=account_id)
            for account_id in sorted(requested_account_ids)
        )
    session.add_all(memberships)
    session.add(
        Event(
            type="user.access.updated",
            payload={
                "org_id": actor.org_id,
                "actor_user_id": actor.id,
                "target_user_id": target.id,
                "clients": [
                    {"client_id": item.client_id, "role": item.role.value}
                    for item in body.clients
                ],
                "projects": [
                    {"project_id": item.project_id, "role": item.role.value}
                    for item in body.projects
                ],
                "account_scope_mode": body.account_scope_mode,
                "account_ids": sorted(requested_account_ids)
                if body.account_scope_mode == "selected"
                else [],
            },
        )
    )
    await session.commit()
    return await build_user_detail(session, target)
