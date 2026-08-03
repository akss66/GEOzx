"""Approval visibility and decision permissions across task and content ledgers."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import require_account_access, require_project_access
from app.models import Account, BrainTask, ContentItem, ProjectAccount, TaskBrief
from app.models.enums import UserRole, WorkspaceRole

APPROVAL_ROLES = {WorkspaceRole.LEAD, WorkspaceRole.REVIEWER}


async def task_project_ids(session: AsyncSession, task: BrainTask) -> set[int]:
    explicit_project_ids: set[int] = set()
    account_ids: set[int] = set()
    if task.content_item_id is not None:
        content_item = await session.get(ContentItem, task.content_item_id)
        if content_item is not None:
            if content_item.project_id is not None:
                explicit_project_ids.add(content_item.project_id)
            if content_item.account_id is not None:
                account_ids.add(content_item.account_id)

    brief = await session.scalar(select(TaskBrief).where(TaskBrief.task_id == task.id))
    if brief is not None:
        if brief.project_id is not None:
            explicit_project_ids.add(brief.project_id)
        account_ids.update(value for value in brief.account_ids if isinstance(value, int))
    if explicit_project_ids:
        return explicit_project_ids

    project_ids: set[int] = set()
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


async def task_account_ids(session: AsyncSession, task: BrainTask) -> set[int]:
    """Return every account whose data is represented by the task."""
    account_ids: set[int] = set()
    if task.content_item_id is not None:
        content_item = await session.get(ContentItem, task.content_item_id)
        if content_item is not None and content_item.account_id is not None:
            account_ids.add(content_item.account_id)
    brief = await session.scalar(select(TaskBrief).where(TaskBrief.task_id == task.id))
    if brief is not None:
        account_ids.update(value for value in brief.account_ids if isinstance(value, int))
    return account_ids


async def require_task_visibility(
    session: AsyncSession,
    user,
    task: BrainTask,
) -> int | None:
    """Require every account boundary before exposing task data or decisions."""
    project_ids = await task_project_ids(session, task)
    if user.role != UserRole.ADMIN:
        account_ids = await task_account_ids(session, task)
        for account_id in account_ids:
            await require_account_access(session, user, account_id)
        if account_ids:
            return next(iter(project_ids), None)
        visible_projects: list[int] = []
        for project_id in project_ids:
            try:
                await require_project_access(session, user, project_id)
            except HTTPException as exc:
                if exc.status_code == status.HTTP_404_NOT_FOUND:
                    continue
                raise
            visible_projects.append(project_id)
        if project_ids and not visible_projects:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
        return visible_projects[0] if visible_projects else None
    return next(iter(project_ids), None)


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
    visible_project_id = await require_task_visibility(session, user, task)
    project_ids = await task_project_ids(session, task)
    if user.role == UserRole.ADMIN:
        return visible_project_id
    if not project_ids:
        account_ids = await task_account_ids(session, task)
        if task.created_by_id == user.id and account_ids:
            for account_id in account_ids:
                await require_account_access(session, user, account_id)
            return None
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该审批没有可验证的项目上下文",
        )
    for project_id in project_ids:
        if await can_decide_project(session, user, project_id):
            return project_id
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权处理该审批")
