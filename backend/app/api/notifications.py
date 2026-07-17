from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import Notification
from app.schemas.shell import NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    user: CurrentUser,
    session: SessionDep,
    unread_only: Annotated[bool, Query()] = False,
) -> list[NotificationOut]:
    query = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    rows = await session.scalars(query.order_by(Notification.id.desc()).limit(50))
    return [NotificationOut.model_validate(row) for row in rows]


@router.get("/unread-count")
async def get_unread_count(user: CurrentUser, session: SessionDep) -> dict[str, int]:
    count = await session.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
        )
    )
    return {"count": int(count or 0)}


@router.patch("/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: int, user: CurrentUser, session: SessionDep
) -> NotificationOut:
    notice = await session.get(Notification, notification_id)
    if notice is None or notice.user_id != user.id or notice.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通知不存在")
    if notice.read_at is None:
        notice.read_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(notice)
    return NotificationOut.model_validate(notice)
