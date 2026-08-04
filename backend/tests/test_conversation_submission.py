from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

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
