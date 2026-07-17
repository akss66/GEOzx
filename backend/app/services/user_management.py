"""User lifecycle, workspace authorization, and audit helpers."""

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Client,
    ClientMembership,
    Event,
    Project,
    ProjectMembership,
    User,
)
from app.models.enums import UserRole
from app.schemas.auth import (
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
    return UserDetailOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        has_global_access=user.role == UserRole.ADMIN,
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
        active_admins = await session.scalar(
            select(func.count(User.id)).where(
                User.org_id == actor.org_id,
                User.role == UserRole.ADMIN,
                User.is_active.is_(True),
            )
        )
        if (active_admins or 0) <= 1:
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

    await session.execute(
        delete(ClientMembership).where(ClientMembership.user_id == target.id)
    )
    await session.execute(
        delete(ProjectMembership).where(ProjectMembership.user_id == target.id)
    )
    session.add_all(
        [
            ClientMembership(
                client_id=item.client_id,
                user_id=target.id,
                role=item.role,
            )
            for item in body.clients
        ]
        + [
            ProjectMembership(
                project_id=item.project_id,
                user_id=target.id,
                role=item.role,
            )
            for item in body.projects
        ]
    )
    session.add(
        Event(
            type="user.access.updated",
            payload={
                "org_id": actor.org_id,
                "actor_user_id": actor.id,
                "target_user_id": target.id,
                "clients": [item.model_dump(mode="json") for item in body.clients],
                "projects": [item.model_dump(mode="json") for item in body.projects],
            },
        )
    )
    await session.commit()
    return await build_user_detail(session, target)
