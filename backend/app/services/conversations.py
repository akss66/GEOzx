"""Authorized, account-scoped conversation persistence."""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.workspace_access import require_account_access
from app.models import (
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    Event,
    LLMCall,
    SkillRun,
    User,
)
from app.schemas.conversation import (
    CreateConversationThreadRequest,
    CreateConversationTurnRequest,
)

_BLOCKING_RUN_DELETE_STATUSES = {
    "claimed",
    "queued",
    "running",
    "retry_wait",
    "waiting_permission",
    "waiting_decision",
    "waiting_user",
    "waiting_predecessor",
    "stopped",
}

_BLOCKING_SKILL_DELETE_STATUSES = {
    "running",
    "retry_wait",
    "waiting_permission",
    "stopped",
}

_DELETABLE_CONVERSATION_EVENT_TYPES = {
    "brain.runtime.user_message",
    "brain.runtime.message_start",
    "brain.runtime.message_delta",
    "brain.runtime.message_done",
    "brain.runtime.message_error",
    "brain.runtime.memory_compacted",
}

_TECHNICAL_EVENT_TYPE_PREFIXES = (
    "agent.harness.",
    "agent.kernel.",
)


def _thread_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="对话不存在",
    )


def _client_message_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "CLIENT_MESSAGE_CONFLICT",
            "message": "client_message_id 已用于另一条消息",
        },
    )


def _active_delete_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "CONVERSATION_DELETE_BLOCKED",
            "message": "Conversation cannot be deleted while runtime work is still active.",
        },
    )


@dataclass(frozen=True)
class ConversationThreadSummary:
    thread: ConversationThread
    turn_count: int
    last_message: str


async def create_conversation_thread(
    session: AsyncSession,
    user: User,
    input: CreateConversationThreadRequest,
) -> ConversationThread:
    """Create a Thread only after resolving the account through workspace access."""
    account = await require_account_access(session, user, input.account_id)
    thread = ConversationThread(
        org_id=user.org_id,
        created_by_id=user.id,
        client_id=account.client_id,
        project_id=account.project_id,
        account_id=account.id,
        title=input.title,
    )
    session.add(thread)
    await session.flush()
    return thread


async def get_conversation_thread(
    session: AsyncSession,
    user: User,
    thread_id: int,
) -> ConversationThread:
    """Resolve a Thread without revealing another organization or account."""
    thread = await session.scalar(
        select(ConversationThread).where(
            ConversationThread.id == thread_id,
            ConversationThread.org_id == user.org_id,
            ConversationThread.created_by_id == user.id,
        )
    )
    if thread is None:
        raise _thread_not_found()
    try:
        await require_account_access(session, user, thread.account_id)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise _thread_not_found() from exc
        raise
    return thread


async def append_conversation_turn(
    session: AsyncSession,
    user: User,
    thread_id: int,
    input: CreateConversationTurnRequest,
) -> tuple[ConversationTurn, bool]:
    """Append one idempotent Turn through the authorized Thread boundary."""
    thread = await get_conversation_thread(session, user, thread_id)
    existing = await _find_turn(session, thread.id, input.client_message_id)
    if existing is not None:
        _require_same_message(existing, input.message)
        return existing, False

    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=user.org_id,
        created_by_id=user.id,
        client_message_id=input.client_message_id,
        user_input=input.message,
    )
    if not thread.title.strip():
        thread.title = input.message.strip()[:80]
    thread.updated_at = datetime.now(UTC)
    try:
        async with session.begin_nested():
            session.add(turn)
            await session.flush()
    except IntegrityError:
        existing = await _find_turn(session, thread.id, input.client_message_id)
        if existing is None:
            raise
        _require_same_message(existing, input.message)
        return existing, False
    return turn, True


async def list_conversation_threads(
    session: AsyncSession,
    user: User,
    *,
    account_id: int,
) -> list[ConversationThreadSummary]:
    """List only the current user's conversations for one authorized account."""

    await require_account_access(session, user, account_id)
    latest_turn = aliased(ConversationTurn)
    last_message = (
        select(latest_turn.user_input)
        .where(latest_turn.thread_id == ConversationThread.id)
        .order_by(latest_turn.id.desc())
        .limit(1)
        .correlate(ConversationThread)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(
                ConversationThread,
                func.count(ConversationTurn.id),
                last_message,
            )
            .outerjoin(
                ConversationTurn,
                ConversationTurn.thread_id == ConversationThread.id,
            )
            .where(
                ConversationThread.org_id == user.org_id,
                ConversationThread.created_by_id == user.id,
                ConversationThread.account_id == account_id,
            )
            .group_by(ConversationThread.id)
            .order_by(ConversationThread.updated_at.desc(), ConversationThread.id.desc())
        )
    ).all()
    return [
        ConversationThreadSummary(
            thread=row[0],
            turn_count=int(row[1] or 0),
            last_message=str(row[2] or ""),
        )
        for row in rows
    ]


async def delete_conversation_thread(
    session: AsyncSession,
    user: User,
    thread_id: int,
) -> None:
    """Permanently delete one owned conversation and its execution traces."""

    thread = await get_conversation_thread(session, user, thread_id)
    active_run = await session.scalar(
        select(AgentRun.id).where(
            AgentRun.org_id == user.org_id,
            AgentRun.thread_id == thread.id,
            AgentRun.status.in_(_BLOCKING_RUN_DELETE_STATUSES),
        )
    )
    active_skill_run = await session.scalar(
        select(SkillRun.id).where(
            SkillRun.org_id == user.org_id,
            SkillRun.thread_id == thread.id,
            SkillRun.status.in_(_BLOCKING_SKILL_DELETE_STATUSES),
        )
    )
    if active_run is not None or active_skill_run is not None:
        raise _active_delete_conflict()
    run_rows = (
        await session.execute(
            select(AgentRun.id, AgentRun.task_id).where(
                AgentRun.org_id == user.org_id,
                AgentRun.thread_id == thread.id,
            )
        )
    ).all()
    run_ids = [int(row[0]) for row in run_rows]
    task_ids = {int(row[1]) for row in run_rows if row[1] is not None}
    task_ids.update(
        int(task_id)
        for task_id in await session.scalars(
            select(SkillRun.task_id).where(
                SkillRun.org_id == user.org_id,
                SkillRun.thread_id == thread.id,
                SkillRun.task_id.is_not(None),
            )
        )
        if task_id is not None
    )
    event_scope = Event.thread_id == thread.id
    if run_ids:
        event_scope = or_(event_scope, Event.run_id.in_(run_ids))
    deletable_event_type = or_(
        Event.type.in_(_DELETABLE_CONVERSATION_EVENT_TYPES),
        *(Event.type.like(f"{prefix}%") for prefix in _TECHNICAL_EVENT_TYPE_PREFIXES),
    )
    await session.execute(
        delete(Event).where(and_(event_scope, deletable_event_type))
    )
    await session.execute(
        update(Event)
        .where(and_(event_scope, ~deletable_event_type))
        .values(
            thread_id=None,
            turn_id=None,
            run_id=None,
            skill_run_id=None,
        )
    )
    await session.execute(
        delete(LLMCall).where(
            or_(
                LLMCall.trace_id == f"conversation-thread-{thread.id}",
                *(
                    [LLMCall.task_id.in_(task_ids)]
                    if task_ids
                    else []
                ),
            )
        )
    )
    await session.execute(
        update(Deliverable)
        .where(Deliverable.thread_id == thread.id)
        .values(
            thread_id=None,
            turn_id=None,
            run_id=None,
            skill_run_id=None,
        )
    )
    await session.execute(
        delete(AgentToolCall).where(AgentToolCall.thread_id == thread.id)
    )
    await session.execute(
        delete(AgentInvocation).where(AgentInvocation.thread_id == thread.id)
    )
    await session.execute(delete(SkillRun).where(SkillRun.thread_id == thread.id))
    await session.execute(delete(AgentRun).where(AgentRun.thread_id == thread.id))
    await session.delete(thread)
    await session.commit()


async def _find_turn(
    session: AsyncSession,
    thread_id: int,
    client_message_id: str,
) -> ConversationTurn | None:
    return await session.scalar(
        select(ConversationTurn).where(
            ConversationTurn.thread_id == thread_id,
            ConversationTurn.client_message_id == client_message_id,
        )
    )


def _require_same_message(turn: ConversationTurn, message: str) -> None:
    if turn.user_input != message:
        raise _client_message_conflict()
