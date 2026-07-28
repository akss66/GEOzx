"""Additive Thread and Turn endpoints for the main-Agent V2 runtime."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import CurrentUser
from app.db import get_session
from app.models import ConversationThread, ConversationTurn
from app.schemas.conversation import (
    ConversationAgentRunOut,
    ConversationThreadOut,
    ConversationTurnOut,
    CreateConversationThreadRequest,
    CreateConversationTurnRequest,
    TurnSubmissionOut,
)
from app.services.agent_runs import claim_agent_run, get_agent_run
from app.services.conversations import (
    append_conversation_turn,
    create_conversation_thread,
    get_conversation_thread,
)

router = APIRouter(prefix="/brain", tags=["brain-conversations"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _require_v2_enabled() -> None:
    if settings.main_agent_v2_enabled:
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "MAIN_AGENT_V2_DISABLED",
            "message": "Main Agent V2 is disabled",
        },
    )


def _turn_out(turn: ConversationTurn) -> ConversationTurnOut:
    return ConversationTurnOut.model_validate(turn)


def _thread_out(
    thread: ConversationThread,
    turns: list[ConversationTurn],
) -> ConversationThreadOut:
    return ConversationThreadOut(
        id=thread.id,
        org_id=thread.org_id,
        created_by_id=thread.created_by_id,
        client_id=thread.client_id,
        project_id=thread.project_id,
        account_id=thread.account_id,
        title=thread.title,
        turns=[_turn_out(turn) for turn in turns],
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


async def _ordered_turns(
    session: AsyncSession,
    thread: ConversationThread,
) -> list[ConversationTurn]:
    return list(
        await session.scalars(
            select(ConversationTurn)
            .where(
                ConversationTurn.thread_id == thread.id,
                ConversationTurn.org_id == thread.org_id,
            )
            .order_by(ConversationTurn.id)
        )
    )


@router.post(
    "/conversations",
    response_model=ConversationThreadOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread(
    body: CreateConversationThreadRequest,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationThreadOut:
    _require_v2_enabled()
    thread = await create_conversation_thread(session, user, body)
    await session.commit()
    await session.refresh(thread)
    return _thread_out(thread, [])


@router.get(
    "/conversations/{thread_id}",
    response_model=ConversationThreadOut,
)
async def get_thread(
    thread_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationThreadOut:
    _require_v2_enabled()
    thread = await get_conversation_thread(session, user, thread_id)
    return _thread_out(thread, await _ordered_turns(session, thread))


@router.post(
    "/conversations/{thread_id}/turns",
    response_model=TurnSubmissionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_turn(
    thread_id: int,
    body: CreateConversationTurnRequest,
    user: CurrentUser,
    session: SessionDep,
) -> TurnSubmissionOut:
    _require_v2_enabled()
    thread = await get_conversation_thread(session, user, thread_id)
    existing_run = await get_agent_run(
        session,
        org_id=user.org_id,
        requested_by_id=user.id,
        client_message_id=body.client_message_id,
    )
    if existing_run is not None and existing_run.thread_id != thread.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CLIENT_MESSAGE_CONFLICT",
                "message": "client_message_id is already bound to another request",
            },
        )
    turn, created = await append_conversation_turn(
        session,
        user,
        thread.id,
        body,
    )
    request_payload = {
        "account_id": thread.account_id,
        "attachment_ids": body.attachment_ids,
        "client_message_id": body.client_message_id,
        "execution_preference": body.execution_preference,
        "message": body.message,
        "requested_skill_code": body.requested_skill_code,
        "thread_id": thread.id,
        "turn_id": turn.id,
    }
    try:
        run, _claimed = await claim_agent_run(
            session,
            org_id=user.org_id,
            requested_by_id=user.id,
            client_message_id=body.client_message_id,
            request_payload=request_payload,
            thread_id=thread.id,
            turn_id=turn.id,
        )
    except HTTPException:
        if created:
            await session.rollback()
        raise
    turn_out = _turn_out(turn)
    return TurnSubmissionOut(
        turn=turn_out,
        run=ConversationAgentRunOut.model_validate(run),
        task_id=None,
        projections=turn_out.projections,
    )


@router.get(
    "/turns/{turn_id}",
    response_model=ConversationTurnOut,
)
async def get_turn(
    turn_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationTurnOut:
    _require_v2_enabled()
    turn = await session.scalar(
        select(ConversationTurn).where(
            ConversationTurn.id == turn_id,
            ConversationTurn.org_id == user.org_id,
        )
    )
    if turn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation turn not found",
        )
    await get_conversation_thread(session, user, turn.thread_id)
    return _turn_out(turn)
