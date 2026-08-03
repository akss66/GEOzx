"""Authorized, account-scoped conversation persistence."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.workspace_access import require_account_access
from app.models import (
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    AuditRecord,
    BrainTask,
    ConversationAttachment,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    DeliverableAcceptance,
    Event,
    LLMCall,
    SkillRun,
    ToolExecutionAttempt,
    User,
)
from app.models.enums import AgentInvocationStatus, DeliverableStatus
from app.schemas.conversation import (
    ConversationDeletionSummary,
    CreateConversationThreadRequest,
    CreateConversationTurnRequest,
)
from app.services.attachments import remove_attachment_objects, restore_attachment_objects
from app.services.runtime_state import runtime_status_family

_TERMINAL_INVOCATION_STATUSES = {
    AgentInvocationStatus.DONE,
    AgentInvocationStatus.FAILED,
    AgentInvocationStatus.BLOCKED,
}
_TERMINAL_TOOL_STATUSES = {"success", "failed"}
_TERMINAL_ATTEMPT_STATUSES = {"success", "failed"}


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
) -> ConversationDeletionSummary:
    """Permanently delete one terminal, owned conversation and its private traces."""

    try:
        thread = await session.scalar(
            select(ConversationThread)
            .where(
                ConversationThread.id == thread_id,
                ConversationThread.org_id == user.org_id,
                ConversationThread.created_by_id == user.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if thread is None:
            raise _thread_not_found()
        try:
            await require_account_access(session, user, thread.account_id)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                raise _thread_not_found() from exc
            raise

        turns = list(
            await session.scalars(
                select(ConversationTurn)
                .where(ConversationTurn.thread_id == thread.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        runs = list(
            await session.scalars(
                select(AgentRun)
                .where(AgentRun.thread_id == thread.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        run_ids = [row.id for row in runs]
        skill_runs = list(
            await session.scalars(
                select(SkillRun)
                .where(SkillRun.thread_id == thread.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        skill_run_ids = [row.id for row in skill_runs]
        invocation_scope = [AgentInvocation.thread_id == thread.id]
        if run_ids:
            invocation_scope.append(AgentInvocation.run_id.in_(run_ids))
        if skill_run_ids:
            invocation_scope.append(AgentInvocation.skill_run_id.in_(skill_run_ids))
        invocations = list(
            await session.scalars(
                select(AgentInvocation)
                .where(or_(*invocation_scope))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        invocation_ids = [row.id for row in invocations]
        tool_scope = [AgentToolCall.thread_id == thread.id]
        if invocation_ids:
            tool_scope.append(AgentToolCall.invocation_id.in_(invocation_ids))
        if skill_run_ids:
            tool_scope.append(AgentToolCall.skill_run_id.in_(skill_run_ids))
        tool_calls = list(
            await session.scalars(
                select(AgentToolCall)
                .where(or_(*tool_scope))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        tool_call_ids = [row.id for row in tool_calls]
        attempts = (
            list(
                await session.scalars(
                    select(ToolExecutionAttempt)
                    .where(ToolExecutionAttempt.tool_call_id.in_(tool_call_ids))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            if tool_call_ids
            else []
        )

        task_ids = {
            row.task_id
            for row in [*runs, *skill_runs, *invocations, *tool_calls]
            if row.task_id is not None
        }
        tasks = (
            list(
                await session.scalars(
                    select(BrainTask)
                    .where(BrainTask.id.in_(task_ids))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            if task_ids
            else []
        )
        if (
            any(row.created_by_id != user.id or row.org_id != user.org_id for row in turns)
            or any(row.requested_by_id != user.id or row.org_id != user.org_id for row in runs)
            or any(row.org_id != user.org_id for row in skill_runs)
            or any(row.org_id != user.org_id for row in tool_calls)
            or any(row.created_by_id != user.id or row.org_id != user.org_id for row in tasks)
        ):
            raise _active_delete_conflict()

        if (
            any(not _is_terminal_runtime_status(row.status) for row in turns)
            or any(not _is_terminal_runtime_status(row.status) for row in runs)
            or any(not _is_terminal_skill_status(row.status) for row in skill_runs)
            or any(row.status not in _TERMINAL_INVOCATION_STATUSES for row in invocations)
            or any(row.status not in _TERMINAL_TOOL_STATUSES for row in tool_calls)
            or any(row.status not in _TERMINAL_ATTEMPT_STATUSES for row in attempts)
        ):
            raise _active_delete_conflict()

        turn_ids = [row.id for row in turns]
        event_scope_parts = [Event.thread_id == thread.id]
        if turn_ids:
            event_scope_parts.append(Event.turn_id.in_(turn_ids))
        if run_ids:
            event_scope_parts.append(Event.run_id.in_(run_ids))
        if skill_run_ids:
            event_scope_parts.append(Event.skill_run_id.in_(skill_run_ids))
        event_scope = or_(*event_scope_parts)
        scoped_events = list(
            await session.scalars(
                select(Event)
                .where(event_scope)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

        llm_scope = [LLMCall.trace_id == f"conversation-thread-{thread.id}"]
        llm_scope.extend(LLMCall.trace_id == f"agent-run:{run_id}" for run_id in run_ids)
        if invocation_ids:
            llm_scope.append(LLMCall.invocation_id.in_(invocation_ids))
        scoped_llm_calls = list(
            await session.scalars(
                select(LLMCall).where(
                    LLMCall.org_id == user.org_id,
                    LLMCall.created_by_id == user.id,
                    or_(*llm_scope),
                )
            )
        )
        attachment_rows = list(
            await session.scalars(
                select(ConversationAttachment)
                .where(
                    ConversationAttachment.org_id == user.org_id,
                    ConversationAttachment.created_by_id == user.id,
                    ConversationAttachment.account_id == thread.account_id,
                    ConversationAttachment.thread_id == thread.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        deliverable_scope = or_(
            Deliverable.thread_id == thread.id,
            *([Deliverable.turn_id.in_(turn_ids)] if turn_ids else []),
            *([Deliverable.run_id.in_(run_ids)] if run_ids else []),
            *([Deliverable.skill_run_id.in_(skill_run_ids)] if skill_run_ids else []),
        )
        scoped_deliverables = list(
            await session.scalars(
                select(Deliverable)
                .where(deliverable_scope)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        scoped_deliverable_ids = [row.id for row in scoped_deliverables]
        referenced_deliverable_ids = (
            set(
                await session.scalars(
                    select(DeliverableAcceptance.deliverable_id).where(
                        DeliverableAcceptance.deliverable_id.in_(scoped_deliverable_ids)
                    )
                )
            )
            if scoped_deliverable_ids
            else set()
        )
        draft_deliverable_ids = [
            row.id
            for row in scoped_deliverables
            if row.status != DeliverableStatus.APPROVED and row.id not in referenced_deliverable_ids
        ]
        retained_deliverable_ids = [
            row.id for row in scoped_deliverables if row.id not in draft_deliverable_ids
        ]

        retained_audit_categories = _add_minimal_audit_records(
            session,
            user=user,
            account_id=thread.account_id,
            events=scoped_events,
            tool_calls=tool_calls,
            llm_calls=scoped_llm_calls,
        )

        if scoped_events:
            await session.execute(
                delete(Event).where(Event.id.in_([row.id for row in scoped_events]))
            )
        if scoped_llm_calls:
            await session.execute(
                delete(LLMCall).where(
                    LLMCall.id.in_([row.id for row in scoped_llm_calls]),
                    LLMCall.org_id == user.org_id,
                    LLMCall.created_by_id == user.id,
                )
            )
        if draft_deliverable_ids:
            await session.execute(
                delete(Deliverable).where(Deliverable.id.in_(draft_deliverable_ids))
            )
        if retained_deliverable_ids:
            await session.execute(
                update(Deliverable)
                .where(Deliverable.id.in_(retained_deliverable_ids))
                .values(thread_id=None, turn_id=None, run_id=None, skill_run_id=None)
            )
        attachment_ids = [row.id for row in attachment_rows]
        attachment_storage_keys = [row.storage_key for row in attachment_rows]
        if attachment_ids:
            await session.execute(
                delete(ConversationAttachment).where(
                    ConversationAttachment.id.in_(attachment_ids),
                    ConversationAttachment.org_id == user.org_id,
                    ConversationAttachment.created_by_id == user.id,
                )
            )

        if attempts:
            await session.execute(
                delete(ToolExecutionAttempt).where(
                    ToolExecutionAttempt.id.in_([row.id for row in attempts])
                )
            )
        if tool_call_ids:
            await session.execute(
                delete(AgentToolCall).where(
                    AgentToolCall.id.in_(tool_call_ids),
                    AgentToolCall.org_id == user.org_id,
                )
            )
        if invocation_ids:
            await session.execute(
                delete(AgentInvocation).where(AgentInvocation.id.in_(invocation_ids))
            )
        if skill_run_ids:
            await session.execute(delete(SkillRun).where(SkillRun.id.in_(skill_run_ids)))
        if run_ids:
            await session.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
        await session.delete(thread)
        removed_attachment_objects = remove_attachment_objects(attachment_storage_keys)
        try:
            await session.commit()
        except Exception:
            restore_attachment_objects(removed_attachment_objects)
            raise
        return ConversationDeletionSummary(
            messages_deleted=len(turns),
            events_deleted=len(scoped_events),
            llm_calls_deleted=len(scoped_llm_calls),
            attachments_deleted=len(attachment_rows),
            draft_artifacts_deleted=len(draft_deliverable_ids),
            retained_audit_categories=retained_audit_categories,
        )
    except HTTPException:
        await session.rollback()
        raise
    except Exception:
        await session.rollback()
        raise


def _add_minimal_audit_records(
    session: AsyncSession,
    *,
    user: User,
    account_id: int,
    events: list[Event],
    tool_calls: list[AgentToolCall],
    llm_calls: list[LLMCall],
) -> list[str]:
    """Copy only allowlisted business facts before deleting private runtime data."""

    categories: set[str] = set()
    for event in events:
        if event.type != "approval.decided":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        approved = payload.get("approved") is True
        approval_kind = str(payload.get("approval_kind") or "approval")[:120]
        session.add(
            AuditRecord(
                org_id=user.org_id,
                account_id=account_id,
                actor_user_id=user.id,
                category="approval",
                action=approval_kind,
                outcome="approved" if approved else "rejected",
                details={"approved": approved},
                occurred_at=event.created_at,
            )
        )
        categories.add("approval")

    publish_calls = [
        row
        for row in tool_calls
        if row.status == "success"
        and row.side_effect_level != "read"
        and "publish" in row.tool_code.lower()
    ]
    for row in publish_calls:
        session.add(
            AuditRecord(
                org_id=user.org_id,
                account_id=account_id,
                actor_user_id=user.id,
                category="publish",
                action=row.tool_code[:120],
                outcome="success",
                details={"provider_status": "success"},
                occurred_at=row.finished_at or row.updated_at,
            )
        )
        categories.add("publish")

    if llm_calls or tool_calls:
        total_cost = sum((Decimal(str(row.cost_usd)) for row in llm_calls), Decimal("0"))
        total_cost += sum((Decimal(row.cost) for row in tool_calls), Decimal("0"))
        session.add(
            AuditRecord(
                org_id=user.org_id,
                account_id=account_id,
                actor_user_id=user.id,
                category="cost",
                action="conversation_runtime_total",
                outcome="recorded",
                amount_usd=total_cost,
                details={"amount_usd": float(total_cost)},
                occurred_at=datetime.now(UTC),
            )
        )
        categories.add("cost")

    return sorted(categories)


def _is_terminal_runtime_status(value: str) -> bool:
    try:
        return runtime_status_family(value) == "terminal"
    except ValueError:
        return False


def _is_terminal_skill_status(value: str) -> bool:
    return value in {"completed", "needs_review", "blocked", "failed", "cancelled"}


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
