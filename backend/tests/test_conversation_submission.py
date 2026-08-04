from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models import ConversationTurn
from app.schemas.conversation import CreateConversationTurnRequest
from app.services.conversation_submission import prepare_conversation_turn_submission
from tests.test_artifacts_api import _seed_artifact


async def test_prepare_submission_keeps_commit_ownership_with_caller(session, admin) -> None:
    seeded = await _seed_artifact(session, admin, account_name="prepare-submission")
    thread = seeded[3]
    session.commit = AsyncMock(side_effect=AssertionError("prepare must not commit"))
    request = CreateConversationTurnRequest(
        client_message_id="prepared-turn",
        message="Prepare the next operating iteration.",
        requested_skill_code="operation_iteration",
        execution_preference="FORMAL_TASK",
    )

    prepared = await prepare_conversation_turn_submission(
        session,
        admin,
        thread,
        request,
        attachment_contexts=[],
        trusted_structured_input={
            "confirmed_review_artifact_id": seeded[8].id,
            "cycle_days": 7,
        },
    )

    assert prepared.claimed is True
    assert prepared.turn.status == "queued"
    assert prepared.run.status == "queued"
    assert prepared.run.request_payload["trusted_structured_input"] == {
        "confirmed_review_artifact_id": seeded[8].id,
        "cycle_days": 7,
    }
    session.commit.assert_not_awaited()


async def test_prepare_submission_rejects_changed_trusted_input_for_same_client_id(
    session, admin
) -> None:
    seeded = await _seed_artifact(session, admin, account_name="prepare-trusted-conflict")
    thread = seeded[3]
    request = CreateConversationTurnRequest(
        client_message_id="prepared-trusted-conflict",
        message="Prepare the next operating iteration.",
        requested_skill_code="operation_iteration",
        execution_preference="FORMAL_TASK",
    )
    await prepare_conversation_turn_submission(
        session,
        admin,
        thread,
        request,
        attachment_contexts=[],
        trusted_structured_input={"confirmed_review_artifact_id": 11, "cycle_days": 7},
    )
    await session.commit()

    with pytest.raises(HTTPException) as raised:
        await prepare_conversation_turn_submission(
            session,
            admin,
            thread,
            request,
            attachment_contexts=[],
            trusted_structured_input={"confirmed_review_artifact_id": 12, "cycle_days": 7},
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "CLIENT_MESSAGE_CONFLICT"


async def test_prepare_submission_rejects_changed_target_for_same_client_id(
    session, admin
) -> None:
    seeded = await _seed_artifact(session, admin, account_name="prepare-target-conflict")
    thread = seeded[3]
    first_target = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="first-target",
        user_input="first active turn",
        status="running",
    )
    second_target = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="second-target",
        user_input="second active turn",
        status="running",
    )
    session.add_all([first_target, second_target])
    await session.commit()
    request = CreateConversationTurnRequest(
        client_message_id="prepared-target-conflict",
        message="先停一下",
        target_turn_id=first_target.id,
    )
    await prepare_conversation_turn_submission(
        session,
        admin,
        thread,
        request,
        attachment_contexts=[],
    )
    await session.commit()

    with pytest.raises(HTTPException) as raised:
        await prepare_conversation_turn_submission(
            session,
            admin,
            thread,
            request.model_copy(update={"target_turn_id": second_target.id}),
            attachment_contexts=[],
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "CLIENT_MESSAGE_CONFLICT"
