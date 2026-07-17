"""Durable approval audit events and in-app notifications."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ClientMembership,
    Event,
    Notification,
    Project,
    ProjectMembership,
    User,
)
from app.models.enums import UserRole, WorkspaceRole

APPROVER_ROLES = {WorkspaceRole.LEAD, WorkspaceRole.REVIEWER}


async def add_approval_requested(
    session: AsyncSession,
    *,
    org_id: int,
    project_id: int | None,
    content_item_id: int | None,
    approval_kind: str,
    source_id: int,
    title: str,
    body: str | None = None,
) -> None:
    session.add(
        Event(
            type="approval.requested",
            project_id=project_id,
            content_item_id=content_item_id,
            payload={
                "approval_kind": approval_kind,
                "source_id": source_id,
                "title": title,
            },
        )
    )
    recipient_ids = await _project_recipient_ids(
        session, org_id=org_id, project_id=project_id, approvers_only=True
    )
    _add_notifications(
        session,
        org_id=org_id,
        user_ids=recipient_ids,
        notification_type="approval.requested",
        title=f"待审批：{title}",
        body=body or "有新的人工审批请求等待处理。",
    )


async def add_approval_decided(
    session: AsyncSession,
    *,
    org_id: int,
    project_id: int | None,
    content_item_id: int | None,
    approval_kind: str,
    source_id: int,
    title: str,
    approved: bool,
    actor_user_id: int,
    comment: str | None = None,
) -> None:
    session.add(
        Event(
            type="approval.decided",
            project_id=project_id,
            content_item_id=content_item_id,
            payload={
                "approval_kind": approval_kind,
                "source_id": source_id,
                "title": title,
                "approved": approved,
                "comment": comment or "",
                "decided_by": actor_user_id,
            },
        )
    )
    recipient_ids = await _project_recipient_ids(
        session, org_id=org_id, project_id=project_id, approvers_only=False
    )
    recipient_ids.discard(actor_user_id)
    decision = "已通过" if approved else "已驳回"
    _add_notifications(
        session,
        org_id=org_id,
        user_ids=recipient_ids,
        notification_type="approval.decided",
        title=f"审批{decision}：{title}",
        body=comment or f"该审批项{decision}。",
    )


async def _project_recipient_ids(
    session: AsyncSession,
    *,
    org_id: int,
    project_id: int | None,
    approvers_only: bool,
) -> set[int]:
    if project_id is None:
        return set(
            await session.scalars(
                select(User.id).where(
                    User.org_id == org_id,
                    User.role == UserRole.ADMIN,
                    User.is_active.is_(True),
                )
            )
        )
    project = await session.get(Project, project_id)
    if project is None or project.org_id != org_id:
        return set()

    project_query = select(ProjectMembership.user_id).where(
        ProjectMembership.project_id == project_id
    )
    if approvers_only:
        project_query = project_query.where(ProjectMembership.role.in_(APPROVER_ROLES))
    recipient_ids = set(await session.scalars(project_query))

    if project.client_id is not None:
        client_query = select(ClientMembership.user_id).where(
            ClientMembership.client_id == project.client_id
        )
        if approvers_only:
            client_query = client_query.where(ClientMembership.role.in_(APPROVER_ROLES))
        recipient_ids.update(await session.scalars(client_query))

    recipient_ids.update(
        await session.scalars(
            select(User.id).where(
                User.org_id == org_id,
                User.role == UserRole.ADMIN,
                User.is_active.is_(True),
            )
        )
    )
    return recipient_ids


def _add_notifications(
    session: AsyncSession,
    *,
    org_id: int,
    user_ids: set[int],
    notification_type: str,
    title: str,
    body: str,
) -> None:
    session.add_all(
        [
            Notification(
                org_id=org_id,
                user_id=user_id,
                type=notification_type,
                title=title,
                body=body,
                path="/approvals",
            )
            for user_id in sorted(user_ids)
        ]
    )
