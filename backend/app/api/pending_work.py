"""Operator pending-work workspace endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.events import publish_realtime_event
from app.db import get_session
from app.schemas.pending_work import PendingWorkCompletion, PendingWorkResponse
from app.services.pending_work import (
    complete_shoot_task,
    list_pending_work,
    publish_schedule_entry,
)

router = APIRouter(prefix="/accounts/{account_id}/pending-work", tags=["pending-work"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=PendingWorkResponse)
async def get_pending_work(
    account_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> PendingWorkResponse:
    return await list_pending_work(session, user=user, account_id=account_id)


@router.post(
    "/shoot-tasks/{shoot_task_id}/complete",
    response_model=PendingWorkCompletion,
)
async def complete_pending_shoot_task(
    account_id: int,
    shoot_task_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> PendingWorkCompletion:
    result = await complete_shoot_task(
        session,
        user=user,
        account_id=account_id,
        shoot_task_id=shoot_task_id,
    )
    await session.commit()
    await _publish_lifecycle(result.event.id, result.event.type, result.event.payload or {})
    return result.response


@router.post(
    "/schedule-entries/{schedule_entry_id}/publish",
    response_model=PendingWorkCompletion,
)
async def publish_pending_schedule_entry(
    account_id: int,
    schedule_entry_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> PendingWorkCompletion:
    result = await publish_schedule_entry(
        session,
        user=user,
        account_id=account_id,
        schedule_entry_id=schedule_entry_id,
    )
    await session.commit()
    await _publish_lifecycle(result.event.id, result.event.type, result.event.payload or {})
    return result.response


async def _publish_lifecycle(
    event_id: int,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    await publish_realtime_event(event_type, payload, event_id=event_id)
