"""Permission resolution for client and project scoped resources."""

from collections.abc import Collection

from fastapi import HTTPException, status
from sqlalchemy import false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models import (
    Account,
    Client,
    ClientMembership,
    Project,
    ProjectAccount,
    ProjectMembership,
    User,
)
from app.models.enums import UserRole, WorkspaceRole


async def accessible_client_ids(session: AsyncSession, user: User) -> set[int]:
    if user.role == UserRole.ADMIN:
        rows = await session.scalars(select(Client.id).where(Client.org_id == user.org_id))
        return set(rows)

    direct = set(
        await session.scalars(
            select(ClientMembership.client_id).where(ClientMembership.user_id == user.id)
        )
    )
    project_scoped = set(
        await session.scalars(
            select(Project.client_id)
            .join(ProjectMembership, ProjectMembership.project_id == Project.id)
            .where(
                ProjectMembership.user_id == user.id,
                Project.org_id == user.org_id,
                Project.client_id.is_not(None),
            )
        )
    )
    return direct | {client_id for client_id in project_scoped if client_id is not None}


async def accessible_project_ids(session: AsyncSession, user: User) -> set[int]:
    if user.role == UserRole.ADMIN:
        rows = await session.scalars(select(Project.id).where(Project.org_id == user.org_id))
        return set(rows)

    direct = set(
        await session.scalars(
            select(ProjectMembership.project_id).where(ProjectMembership.user_id == user.id)
        )
    )
    client_scoped = set(
        await session.scalars(
            select(Project.id)
            .join(ClientMembership, ClientMembership.client_id == Project.client_id)
            .where(
                ClientMembership.user_id == user.id,
                Project.org_id == user.org_id,
            )
        )
    )
    return direct | client_scoped


async def accessible_account_clause(
    session: AsyncSession, user: User
) -> ColumnElement[bool]:
    """Return the account visibility boundary for the current workspace member."""
    if user.role == UserRole.ADMIN:
        return Account.org_id == user.org_id

    direct_client_ids = select(ClientMembership.client_id).where(
        ClientMembership.user_id == user.id
    )
    project_ids = await accessible_project_ids(session, user)
    clauses: list[ColumnElement[bool]] = [Account.client_id.in_(direct_client_ids)]
    if project_ids:
        linked_accounts = select(ProjectAccount.account_id).where(
            ProjectAccount.project_id.in_(project_ids)
        )
        clauses.extend(
            [
                Account.project_id.in_(project_ids),
                Account.id.in_(linked_accounts),
            ]
        )
    return or_(*clauses) if clauses else false()


async def require_client_access(
    session: AsyncSession,
    user: User,
    client_id: int,
    roles: Collection[WorkspaceRole] | None = None,
) -> Client:
    client = await session.get(Client, client_id)
    if client is None or client.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="客户不存在")
    if user.role == UserRole.ADMIN:
        return client

    membership = await session.scalar(
        select(ClientMembership).where(
            ClientMembership.client_id == client_id,
            ClientMembership.user_id == user.id,
        )
    )
    if membership is None:
        project_access = await session.scalar(
            select(ProjectMembership.id)
            .join(Project, Project.id == ProjectMembership.project_id)
            .where(
                Project.client_id == client_id,
                ProjectMembership.user_id == user.id,
            )
            .limit(1)
        )
        if project_access is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="客户不存在")
        if roles is not None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权执行此操作")
        return client

    if roles is not None and membership.role not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权执行此操作")
    return client


async def require_project_access(
    session: AsyncSession,
    user: User,
    project_id: int,
    roles: Collection[WorkspaceRole] | None = None,
) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    if user.role == UserRole.ADMIN:
        return project

    project_membership = await session.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user.id,
        )
    )
    if project_membership is not None:
        if roles is not None and project_membership.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权执行此操作")
        return project

    if project.client_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    client_membership = await session.scalar(
        select(ClientMembership).where(
            ClientMembership.client_id == project.client_id,
            ClientMembership.user_id == user.id,
        )
    )
    if client_membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    if roles is not None and client_membership.role not in roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权执行此操作")
    return project


async def require_account_access(
    session: AsyncSession,
    user: User,
    account_id: int,
    roles: Collection[WorkspaceRole] | None = None,
) -> Account:
    account = await session.get(Account, account_id)
    if account is None or account.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    if user.role == UserRole.ADMIN:
        return account

    memberships: list[WorkspaceRole] = []
    if account.client_id is not None:
        client_membership = await session.scalar(
            select(ClientMembership).where(
                ClientMembership.client_id == account.client_id,
                ClientMembership.user_id == user.id,
            )
        )
        if client_membership is not None:
            memberships.append(client_membership.role)

    project_ids = set(
        await session.scalars(
            select(ProjectAccount.project_id).where(ProjectAccount.account_id == account.id)
        )
    )
    if account.project_id is not None:
        project_ids.add(account.project_id)
    if project_ids:
        memberships.extend(
            await session.scalars(
                select(ProjectMembership.role).where(
                    ProjectMembership.project_id.in_(project_ids),
                    ProjectMembership.user_id == user.id,
                )
            )
        )

    if not memberships:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    if roles is not None and not any(role in roles for role in memberships):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权执行此操作")
    return account
