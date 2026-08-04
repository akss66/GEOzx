"""Explicit business actions executed from versioned deliverables."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.schemas.deliverable_actions import (
    DeliverableActionExecutionOut,
    DeliverableActionRequest,
)
from app.services.deliverable_actions import execute_deliverable_action

router = APIRouter(tags=["deliverable-actions"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "/artifacts/{artifact_id}/actions/{action_code}",
    response_model=DeliverableActionExecutionOut,
)
async def execute_action(
    artifact_id: int,
    action_code: str,
    body: DeliverableActionRequest,
    user: CurrentUser,
    session: SessionDep,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=160),
    ],
) -> DeliverableActionExecutionOut:
    return await execute_deliverable_action(
        session,
        user,
        artifact_id=artifact_id,
        action_code=action_code,
        idempotency_key=idempotency_key,
        body=body,
    )

