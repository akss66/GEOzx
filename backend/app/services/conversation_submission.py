"""Transaction-neutral preparation of one durable conversation submission."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun, ConversationThread, ConversationTurn, User
from app.orchestrator.skills.registry import skill_registry
from app.schemas.attachment import AttachmentContext
from app.schemas.conversation import CreateConversationTurnRequest
from app.services.agent_runs import claim_agent_run_record, mark_agent_run_queued_record
from app.services.conversations import append_conversation_turn
from app.services.turn_steering import TurnSteeringDecision, bind_turn_steering


@dataclass(frozen=True)
class PreparedTurnSubmission:
    turn: ConversationTurn
    run: AgentRun
    claimed: bool


async def prepare_conversation_turn_submission(
    session: AsyncSession,
    user: User,
    thread: ConversationThread,
    request: CreateConversationTurnRequest,
    attachment_contexts: list[AttachmentContext],
    *,
    trusted_structured_input: dict | None = None,
    steering_decision: TurnSteeringDecision | None = None,
) -> PreparedTurnSubmission:
    """Flush one Turn and queued AgentRun while leaving commit to the caller."""

    normalized_trusted_input: dict | None = None
    if trusted_structured_input is not None:
        requested_skill_code = (request.requested_skill_code or "").strip()
        definition = skill_registry.get(requested_skill_code)
        normalized_trusted_input = definition.input_model.model_validate(
            trusted_structured_input
        ).model_dump(mode="json", exclude_none=True)

    async with session.begin_nested():
        turn, created = await append_conversation_turn(session, user, thread.id, request)
        if created and steering_decision is not None:
            bind_turn_steering(turn, steering_decision)
            await session.flush()
        request_payload = {
            "account_id": thread.account_id,
            "attachment_ids": [item.id for item in attachment_contexts],
            "attachment_contexts": [
                item.model_dump(mode="json") for item in attachment_contexts
            ],
            "client_message_id": request.client_message_id,
            "execution_preference": request.execution_preference,
            "message": request.message,
            "requested_skill_code": request.requested_skill_code,
            "target_turn_id": request.target_turn_id,
            "thread_id": thread.id,
            "turn_id": turn.id,
        }
        if normalized_trusted_input is not None:
            request_payload["trusted_structured_input"] = normalized_trusted_input
        run, claimed = await claim_agent_run_record(
            session,
            org_id=user.org_id,
            requested_by_id=user.id,
            client_message_id=request.client_message_id,
            request_payload=request_payload,
            thread_id=thread.id,
            turn_id=turn.id,
        )
        if claimed:
            run = await mark_agent_run_queued_record(
                session,
                run.id,
                task_id=None,
            )
    return PreparedTurnSubmission(turn=turn, run=run, claimed=claimed)


__all__ = ["PreparedTurnSubmission", "prepare_conversation_turn_submission"]
