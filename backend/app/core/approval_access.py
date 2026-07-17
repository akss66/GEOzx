"""Approval visibility and decision permissions across task and content ledgers."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import require_project_access
from app.models import Account, BrainTask, ContentItem, ProjectAccount, TaskBrief
from app.models.enums import UserRole, WorkspaceRole

APPROVAL_ROLES = {WorkspaceRole.LEAD, WorkspaceRole.REVIEWER}


async def task_project_ids(session: AsyncSession, task: BrainTask) -> set[int]:
    if task.content_item_id is not None:
        content_item = await session.get(ContentItem, task.content_item_id)
        if content_item is not None:
            return {content_item.project_id}

    brief = await session.scalar(select(TaskBrief).where(TaskBrief.task_id == task.id))
    if brief is None:
        return set()
    if brief.project_id is not None:
        return {brief.project_id}

    project_ids: set[int] = set()
    account_ids = [value for value in brief.account_ids if isinstance(value, int)]
    if account_ids:
        project_ids.update(
            await session.scalars(
                select(ProjectAccount.project_id).where(ProjectAccount.account_id.in_(account_ids))
            )
        )
        project_ids.update(
            project_id
            for project_id in await session.scalars(
                select(Account.project_id).where(
                    Account.id.in_(account_ids),
                    Account.project_id.is_not(None),
                )
            )
            if project_id is not None
        )
    return project_ids


async def can_decide_project(session: AsyncSession, user, project_id: int) -> bool:
    if user.role == UserRole.ADMIN:
        return True
    try:
        await require_project_access(session, user, project_id, roles=APPROVAL_ROLES)
    except HTTPException:
        return False
    return True


async def require_task_approval_access(
    session: AsyncSession,
    user,
    task: BrainTask,
) -> int | None:
    project_ids = await task_project_ids(session, task)
    if user.role == UserRole.ADMIN:
        return next(iter(project_ids), None)
    if not project_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该审批没有可验证的项目上下文",
        )
    for project_id in project_ids:
        if await can_decide_project(session, user, project_id):
            return project_id
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权处理该审批")
