"""Additive Thread and Turn endpoints for the main-Agent V2 runtime."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import CurrentUser
from app.db import get_session
from app.models import (
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    ConversationThread,
    ConversationTurn,
    SkillRun,
)
from app.schemas.conversation import (
    ConversationAgentRunOut,
    ConversationThreadListOut,
    ConversationThreadOut,
    ConversationThreadSummaryOut,
    ConversationTurnOut,
    CreateConversationThreadRequest,
    CreateConversationTurnRequest,
    TurnSubmissionOut,
)
from app.services.agent_runs import (
    claim_agent_run,
    enqueue_agent_runtime,
    get_agent_run,
    mark_agent_run_queued,
)
from app.services.conversations import (
    append_conversation_turn,
    create_conversation_thread,
    delete_conversation_thread,
    get_conversation_thread,
    list_conversation_threads,
)

router = APIRouter(prefix="/brain", tags=["brain-conversations"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
_MAIN_AGENT_V2_DISABLED_DETAIL = {
    "code": "MAIN_AGENT_V2_DISABLED",
    "message": "Main Agent V2 is disabled",
}
def _require_v2_enabled() -> None:
    if settings.main_agent_v2_enabled:
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_MAIN_AGENT_V2_DISABLED_DETAIL,
    )


def _require_v2_rollout_access(user: CurrentUser) -> None:
    _require_v2_enabled()


def _turn_out(
    turn: ConversationTurn,
    projections: list[dict] | None = None,
) -> ConversationTurnOut:
    values = ConversationTurnOut.model_validate(turn)
    return values.model_copy(
        update={"projections": _bind_projections_to_turn(turn.id, projections)}
    )


def _bind_projections_to_turn(
    turn_id: int,
    projections: list[dict] | None,
) -> list[dict]:
    return [
        {**projection, "turn_id": turn_id}
        for projection in (projections or [])
        if isinstance(projection, dict)
    ]


async def _thread_out(
    session: AsyncSession,
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
        turns=[
            _turn_out(turn, await _turn_projections(session, turn))
            for turn in turns
        ],
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


async def _turn_projections(
    session: AsyncSession,
    turn: ConversationTurn,
) -> list[dict]:
    payload = await session.scalar(
        select(AgentRun.result_payload)
        .where(
            AgentRun.org_id == turn.org_id,
            AgentRun.thread_id == turn.thread_id,
            AgentRun.turn_id == turn.id,
        )
        .order_by(AgentRun.id.desc())
        .limit(1)
    )
    projections = (
        list(payload.get("projections", []))
        if isinstance(payload, dict) and isinstance(payload.get("projections"), list)
        else []
    )
    execution_summary = await _execution_summary_projection(session, turn)
    if execution_summary is not None:
        projections.append(execution_summary)
    return projections


async def _execution_summary_projection(
    session: AsyncSession,
    turn: ConversationTurn,
) -> dict | None:
    """Expose business provenance while keeping raw execution traces collapsed."""

    run_id = await session.scalar(
        select(AgentRun.id)
        .where(
            AgentRun.org_id == turn.org_id,
            AgentRun.thread_id == turn.thread_id,
            AgentRun.turn_id == turn.id,
        )
        .order_by(AgentRun.id.desc())
        .limit(1)
    )
    skill_run = await session.scalar(
        select(SkillRun)
        .where(
            SkillRun.org_id == turn.org_id,
            SkillRun.thread_id == turn.thread_id,
            SkillRun.turn_id == turn.id,
        )
        .order_by(SkillRun.id.desc())
        .limit(1)
    )
    invocations = list(
        await session.scalars(
            select(AgentInvocation)
            .where(
                AgentInvocation.thread_id == turn.thread_id,
                AgentInvocation.turn_id == turn.id,
            )
            .order_by(AgentInvocation.id)
        )
    )
    tool_calls = list(
        await session.scalars(
            select(AgentToolCall)
            .where(
                AgentToolCall.org_id == turn.org_id,
                AgentToolCall.thread_id == turn.thread_id,
                AgentToolCall.turn_id == turn.id,
            )
            .order_by(AgentToolCall.id)
        )
    )
    if skill_run is None and not invocations and not tool_calls:
        return None
    return {
        "type": "execution_summary",
        "run_id": run_id,
        "skill_code": skill_run.skill_code if skill_run is not None else None,
        "skill_run_id": skill_run.id if skill_run is not None else None,
        "status": skill_run.status if skill_run is not None else None,
        "quality_score": (
            float(skill_run.quality_score)
            if skill_run is not None and skill_run.quality_score is not None
            else None
        ),
        "experts": [
            {
                "id": invocation.id,
                "agent_code": invocation.agent_code.value,
                "agent_name": invocation.agent_name,
                "status": invocation.status.value,
            }
            for invocation in invocations
        ],
        "tools": [
            {
                "id": tool_call.id,
                "tool_code": tool_call.tool_code,
                "tool_name": tool_call.tool_name,
                "status": tool_call.status,
            }
            for tool_call in tool_calls
        ],
    }


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
    _require_v2_rollout_access(user)
    thread = await create_conversation_thread(session, user, body)
    await session.commit()
    await session.refresh(thread)
    return await _thread_out(session, thread, [])


@router.get(
    "/conversations",
    response_model=ConversationThreadListOut,
)
async def list_threads(
    account_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationThreadListOut:
    _require_v2_rollout_access(user)
    rows = await list_conversation_threads(
        session,
        user,
        account_id=account_id,
    )
    return ConversationThreadListOut(
        data=[
            ConversationThreadSummaryOut(
                id=row.thread.id,
                account_id=row.thread.account_id,
                title=row.thread.title,
                turn_count=row.turn_count,
                last_message=row.last_message,
                created_at=row.thread.created_at,
                updated_at=row.thread.updated_at,
            )
            for row in rows
        ]
    )


@router.get(
    "/conversations/{thread_id}",
    response_model=ConversationThreadOut,
)
async def get_thread(
    thread_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ConversationThreadOut:
    _require_v2_rollout_access(user)
    thread = await get_conversation_thread(session, user, thread_id)
    return await _thread_out(
        session,
        thread,
        await _ordered_turns(session, thread),
    )


@router.delete(
    "/conversations/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_thread(
    thread_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> None:
    _require_v2_rollout_access(user)
    await delete_conversation_thread(session, user, thread_id)


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
    _require_v2_rollout_access(user)
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
        run, claimed = await claim_agent_run(
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
    if claimed:
        run = await mark_agent_run_queued(
            session,
            run.id,
            task_id=None,
        )
        await enqueue_agent_runtime(run_id=run.id)
    await session.refresh(turn)
    await session.refresh(run)
    projections = await _turn_projections(session, turn)
    turn_out = _turn_out(turn, projections)
    return TurnSubmissionOut(
        turn=turn_out,
        run=ConversationAgentRunOut.model_validate(run),
        task_id=run.task_id,
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
    _require_v2_rollout_access(user)
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
    return _turn_out(turn, await _turn_projections(session, turn))
