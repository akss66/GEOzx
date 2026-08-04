"""Canonical account-scoped human interrupt endpoints."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import AgentRun, TurnInterrupt
from app.schemas.turn_interrupt import (
    ResolveTurnInterruptOut,
    ResolveTurnInterruptRequest,
    StopConversationTurnOut,
    StopConversationTurnRequest,
    TurnInterruptOut,
)
from app.services.agent_runs import abort_agent_runtime, enqueue_agent_runtime
from app.services.conversations import get_conversation_thread
from app.services.runtime_state import (
    publish_runtime_state_intents,
    replay_runtime_state_events,
)
from app.services.turn_interrupts import request_stop, resolve_interrupt

router = APIRouter(tags=["turn-interrupts"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=8, max_length=160),
]
log = logging.getLogger(__name__)
_DISPATCH_DEFERRED = "Your response was saved. The task will resume automatically."


@router.get(
    "/brain/conversations/{thread_id}/turn-interrupts",
    response_model=list[TurnInterruptOut],
)
async def list_conversation_turn_interrupts(
    thread_id: int,
    user: CurrentUser,
    session: SessionDep,
    interrupt_status: Literal[
        "pending",
        "resolved",
        "cancelled",
        "expired",
        "superseded",
    ] = Query(default="pending", alias="status"),
) -> list[TurnInterruptOut]:
    thread = await get_conversation_thread(session, user, thread_id)
    rows = list(
        await session.scalars(
            select(TurnInterrupt)
            .where(
                TurnInterrupt.org_id == user.org_id,
                TurnInterrupt.account_id == thread.account_id,
                TurnInterrupt.thread_id == thread.id,
                TurnInterrupt.status == interrupt_status,
            )
            .order_by(TurnInterrupt.id)
        )
    )
    return [TurnInterruptOut.model_validate(row) for row in rows]


@router.get("/turn-interrupts/{interrupt_id}", response_model=TurnInterruptOut)
async def get_turn_interrupt(
    interrupt_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> TurnInterruptOut:
    row = await session.scalar(
        select(TurnInterrupt)
        .join(AgentRun, AgentRun.id == TurnInterrupt.run_id)
        .where(
            TurnInterrupt.id == interrupt_id,
            TurnInterrupt.org_id == user.org_id,
            AgentRun.requested_by_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interrupt not found")
    return TurnInterruptOut.model_validate(row)


@router.post(
    "/turn-interrupts/{interrupt_id}/resolve",
    response_model=ResolveTurnInterruptOut,
)
async def resolve_turn_interrupt(
    interrupt_id: int,
    body: ResolveTurnInterruptRequest,
    idempotency_key: IdempotencyKey,
    user: CurrentUser,
    session: SessionDep,
) -> ResolveTurnInterruptOut:
    result = await resolve_interrupt(
        session,
        user=user,
        interrupt_id=interrupt_id,
        expected_version=body.expected_version,
        idempotency_key=idempotency_key,
        resolution=body.resolution,
    )
    interrupt_out = TurnInterruptOut.model_validate(result.interrupt)
    run_id = result.run.id
    await session.commit()
    await publish_runtime_state_intents(session, result.publish_intents)
    if result.replay_runtime_events:
        await replay_runtime_state_events(session, run_id=run_id)
    await session.commit()  # close the post-commit read transaction before dispatch
    dispatch_deferred = False
    if result.dispatch_intent is not None:
        try:
            await enqueue_agent_runtime(run_id=result.dispatch_intent.run_id)
        except Exception:  # noqa: BLE001 - queued DB state is the durable outbox
            dispatch_deferred = True
            log.warning(
                "Interrupt resume dispatch deferred",
                extra={"run_id": run_id},
                exc_info=True,
            )
    return ResolveTurnInterruptOut(
        interrupt=interrupt_out,
        run_id=run_id,
        dispatch_deferred=dispatch_deferred,
        dispatch_message=_DISPATCH_DEFERRED if dispatch_deferred else None,
    )


@router.post(
    "/brain/conversations/{thread_id}/turns/{turn_id}/stop",
    response_model=StopConversationTurnOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def stop_conversation_turn(
    thread_id: int,
    turn_id: int,
    body: StopConversationTurnRequest,
    _idempotency_key: IdempotencyKey,
    user: CurrentUser,
    session: SessionDep,
) -> StopConversationTurnOut:
    with session.no_autoflush:
        run = await session.scalar(
            select(AgentRun).where(
                AgentRun.thread_id == thread_id,
                AgentRun.turn_id == turn_id,
                AgentRun.org_id == user.org_id,
                AgentRun.requested_by_id == user.id,
            )
        )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Turn not found")
    result = await request_stop(
        session,
        user=user,
        run_id=run.id,
        reason=body.reason,
    )
    publish_intents = result.publish_intents
    await session.commit()
    await publish_runtime_state_intents(session, publish_intents)
    await session.commit()  # close the post-commit read transaction before dispatch
    dispatch_deferred = False
    try:
        await abort_agent_runtime(result.run_id)
    except Exception:  # noqa: BLE001 - terminal DB state is authoritative
        dispatch_deferred = True
        log.warning("Stopped run abort deferred", extra={"run_id": result.run_id}, exc_info=True)
    return StopConversationTurnOut(
        thread_id=result.thread_id,
        turn_id=result.turn_id,
        run_id=result.run_id,
        dispatch_deferred=dispatch_deferred,
    )
