"""项目路由：运营项目 CRUD。

读取（list/get）任意登录用户可用；增删改限 admin（系统配置职责）。
均按当前用户 org 隔离。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser
from app.db import get_session
from app.models import Project
from app.models.enums import ProjectStatus
from app.schemas.workspace import (
    CreateProjectRequest,
    ProjectOut,
    UpdateProjectRequest,
)

router = APIRouter(prefix="/projects", tags=["projects"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _get_owned(session: AsyncSession, project_id: int, org_id: int) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    return project


@router.get("", response_model=list[ProjectOut])
async def list_projects(user: CurrentUser, session: SessionDep) -> list[ProjectOut]:
    rows = await session.scalars(
        select(Project).where(Project.org_id == user.org_id).order_by(Project.id.desc())
    )
    return [ProjectOut.model_validate(p) for p in rows]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: CreateProjectRequest, admin: AdminUser, session: SessionDep
) -> ProjectOut:
    project = Project(org_id=admin.org_id, name=body.name, description=body.description)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return ProjectOut.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int, body: UpdateProjectRequest, admin: AdminUser, session: SessionDep
) -> ProjectOut:
    project = await _get_owned(session, project_id, admin.org_id)
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(project, key, value)
    await session.commit()
    await session.refresh(project)
    return ProjectOut.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, admin: AdminUser, session: SessionDep) -> None:
    project = await _get_owned(session, project_id, admin.org_id)
    # 软归档优先于物理删除：避免误删带来不可逆影响。
    project.status = ProjectStatus.ARCHIVED
    await session.commit()
