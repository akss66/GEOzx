from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.core.events import turn_event_idempotency_key
from app.models import (
    Account,
    AgentRun,
    ConversationThread,
    ConversationTurn,
    Event,
    Org,
    SkillRun,
    User,
)
from app.models.enums import Platform
from app.services import turn_events as turn_events_service
from app.services.turn_events import (
    TurnEventScope,
    append_turn_event,
    list_turn_events,
)


@pytest.mark.asyncio
async def test_append_scope_lock_compiles_to_postgresql_for_update_of_turn() -> None:
    captured = []

    class EmptyResult:
        def one_or_none(self):
            return None

    class CaptureSession:
        async def execute(self, statement):
            captured.append(statement)
            return EmptyResult()

    with pytest.raises(ValueError, match="scope"):
        await turn_events_service._validate_scope(
            CaptureSession(),
            TurnEventScope(org_id=1, account_id=2, thread_id=3, turn_id=4),
            lock_turn=True,
        )

    sql = str(captured[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE OF conversation_turns" in sql


async def _create_scope(session, suffix: str) -> tuple[TurnEventScope, ConversationTurn]:
    org = Org(name=f"Turn event org {suffix}")
    user = User(
        org=org,
        email=f"turn-events-{suffix}@example.com",
        hashed_password="not-used",
        display_name="Turn event operator",
    )
    account = Account(
        org=org,
        platform=Platform.DOUYIN,
        nickname=f"Turn event account {suffix}",
    )
    session.add_all([user, account])
    await session.flush()

    thread = ConversationThread(
        org_id=org.id,
        created_by_id=user.id,
        account_id=account.id,
        title=f"Turn event thread {suffix}",
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=org.id,
        created_by_id=user.id,
        client_message_id=f"turn-events-{suffix}",
        user_input="Inspect this account.",
    )
    session.add(turn)
    await session.flush()
    run = AgentRun(
        org_id=org.id,
        requested_by_id=user.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=f"turn-events-run-{suffix}",
    )
    session.add(run)
    await session.flush()
    skill_run = SkillRun(
        org_id=org.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        idempotency_key=f"turn-events-skill-{suffix}",
        skill_code="account.inspection",
        skill_version=1,
        status="running",
    )
    session.add(skill_run)
    await session.flush()
    return (
        TurnEventScope(
            org_id=org.id,
            account_id=account.id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            skill_run_id=skill_run.id,
        ),
        turn,
    )


@pytest.mark.asyncio
async def test_append_is_idempotent_and_allocates_strict_per_turn_sequences(session) -> None:
    scope_a, turn_a = await _create_scope(session, "sequence-a")
    scope_b, turn_b = await _create_scope(session, "sequence-b")

    first = await append_turn_event(
        session,
        scope_a,
        "step.started",
        {"step": "read_data"},
        "read-data",
    )
    repeated = await append_turn_event(
        session,
        scope_a,
        "step.started",
        {"step": "read_data"},
        "read-data",
    )
    second = await append_turn_event(
        session,
        scope_a,
        "step.completed",
        {"step": "read_data"},
        "read-data-done",
    )
    other_turn = await append_turn_event(
        session,
        scope_b,
        "turn.received",
        {"status": "queued"},
        "received",
    )

    assert repeated.id == first.id
    assert [first.sequence, second.sequence, other_turn.sequence] == [1, 2, 1]
    assert [turn_a.next_event_sequence, turn_b.next_event_sequence] == [3, 2]
    assert await session.scalar(select(func.count(Event.id))) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatched_field", ["org_id", "account_id", "thread_id", "turn_id"])
async def test_append_rejects_any_mismatched_required_scope_without_side_effects(
    session,
    mismatched_field: str,
) -> None:
    scope, turn = await _create_scope(session, f"mismatch-{mismatched_field}")
    other_scope, _ = await _create_scope(session, f"other-{mismatched_field}")
    invalid = replace(scope, **{mismatched_field: getattr(other_scope, mismatched_field)})

    with pytest.raises(ValueError, match="scope"):
        await append_turn_event(
            session,
            invalid,
            "turn.received",
            {"status": "queued"},
            "invalid-scope",
        )

    await session.refresh(turn)
    assert turn.next_event_sequence == 1
    assert await session.scalar(select(func.count(Event.id))) == 0


@pytest.mark.asyncio
async def test_append_rejects_run_and_skill_run_outside_the_complete_scope(session) -> None:
    scope_a, turn_a = await _create_scope(session, "source-a")
    scope_b, _ = await _create_scope(session, "source-b")

    for invalid in (
        replace(scope_a, run_id=scope_b.run_id, skill_run_id=None),
        replace(scope_a, skill_run_id=scope_b.skill_run_id),
    ):
        with pytest.raises(ValueError, match="scope"):
            await append_turn_event(
                session,
                invalid,
                "step.started",
                {"step": "read_data"},
                f"invalid-source-{invalid.run_id}-{invalid.skill_run_id}",
            )

    await session.refresh(turn_a)
    assert turn_a.next_event_sequence == 1
    assert await session.scalar(select(func.count(Event.id))) == 0


@pytest.mark.asyncio
async def test_append_rejects_skill_run_that_belongs_to_another_run_in_same_turn(session) -> None:
    scope, turn = await _create_scope(session, "skill-run-link")
    original_run = await session.get(AgentRun, scope.run_id)
    assert original_run is not None
    other_run = AgentRun(
        org_id=scope.org_id,
        requested_by_id=original_run.requested_by_id,
        thread_id=scope.thread_id,
        turn_id=scope.turn_id,
        client_message_id="turn-events-other-run",
    )
    session.add(other_run)
    await session.flush()

    with pytest.raises(ValueError, match="scope"):
        await append_turn_event(
            session,
            replace(scope, run_id=other_run.id),
            "step.started",
            {"step": "read_data"},
            "skill-run-does-not-belong-to-run",
        )

    await session.refresh(turn)
    assert turn.next_event_sequence == 1
    assert await session.scalar(select(func.count(Event.id))) == 0


@pytest.mark.asyncio
async def test_append_flushes_without_committing_and_rolls_back_event_and_cursor(session) -> None:
    scope, turn = await _create_scope(session, "rollback")
    await session.commit()

    created = await append_turn_event(
        session,
        scope,
        "turn.received",
        {"status": "queued"},
        "received",
    )
    assert created.id is not None
    assert turn.next_event_sequence == 2

    await session.rollback()
    await session.refresh(turn)
    assert turn.next_event_sequence == 1
    assert await session.scalar(select(func.count(Event.id))) == 0

    retried = await append_turn_event(
        session,
        scope,
        "turn.received",
        {"status": "queued"},
        "received",
    )
    assert retried.sequence == 1


@pytest.mark.asyncio
async def test_non_integrity_insert_failure_cannot_leave_a_committable_cursor_gap(
    session,
    monkeypatch,
) -> None:
    scope, turn = await _create_scope(session, "insert-failure")
    await session.commit()
    real_flush = session.flush

    async def fail_event_insert(*args, **kwargs):
        if any(isinstance(row, Event) for row in session.new):
            raise RuntimeError("simulated Event insert failure")
        return await real_flush(*args, **kwargs)

    monkeypatch.setattr(session, "flush", fail_event_insert)
    with pytest.raises(RuntimeError, match="Event insert failure"):
        await append_turn_event(
            session,
            scope,
            "turn.received",
            {"status": "queued"},
            "insert-failure",
        )

    await session.commit()
    await session.refresh(turn)
    assert turn.next_event_sequence == 1
    assert await session.scalar(select(func.count(Event.id))) == 0


@pytest.mark.asyncio
async def test_list_uses_complete_scope_order_after_id_and_bounded_limit(session) -> None:
    scope_a, _ = await _create_scope(session, "list-a")
    scope_b, _ = await _create_scope(session, "list-b")
    first = await append_turn_event(
        session, scope_a, "turn.received", {"status": "queued"}, "received"
    )
    await append_turn_event(
        session, scope_b, "turn.received", {"status": "queued"}, "received"
    )
    second = await append_turn_event(
        session, scope_a, "step.started", {"step": "read_data"}, "read-data"
    )
    third = await append_turn_event(
        session, scope_a, "step.completed", {"step": "read_data"}, "read-data-done"
    )
    session.add_all(
        [
            Event(
                type="step.completed",
                org_id=scope_b.org_id,
                account_id=scope_a.account_id,
                thread_id=scope_a.thread_id,
                turn_id=scope_a.turn_id,
                run_id=scope_a.run_id,
                skill_run_id=scope_a.skill_run_id,
                sequence=98,
                payload={"step": "wrong_org"},
                idempotency_key="list-wrong-org",
            ),
            Event(
                type="step.completed",
                org_id=scope_a.org_id,
                account_id=scope_b.account_id,
                thread_id=scope_a.thread_id,
                turn_id=scope_a.turn_id,
                run_id=scope_a.run_id,
                skill_run_id=scope_a.skill_run_id,
                sequence=99,
                payload={"step": "wrong_account"},
                idempotency_key="list-wrong-account",
            ),
            Event(
                type="step.completed",
                org_id=scope_a.org_id,
                account_id=scope_a.account_id,
                thread_id=scope_a.thread_id,
                turn_id=scope_a.turn_id,
                run_id=scope_b.run_id,
                skill_run_id=scope_b.skill_run_id,
                sequence=100,
                payload={"step": "wrong_optional_source"},
                idempotency_key="list-wrong-optional-source",
            ),
        ]
    )
    await session.flush()

    assert [row.id for row in await list_turn_events(session, scope_a)] == [
        first.id,
        second.id,
        third.id,
    ]
    assert [row.id for row in await list_turn_events(session, scope_a, after_id=first.id)] == [
        second.id,
        third.id,
    ]
    assert [row.id for row in await list_turn_events(session, scope_a, limit=2)] == [
        first.id,
        second.id,
    ]

    for kwargs in ({"after_id": -1}, {"limit": 0}, {"limit": 501}):
        with pytest.raises(ValueError):
            await list_turn_events(session, scope_a, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        "turn.received",
        "turn.completed",
        "turn.failed",
        "turn.blocked",
        "turn.cancelled",
        "turn.stopped",
        "turn.paused",
        "step.started",
        "step.completed",
        "step.failed",
        "deliverable.updated",
    ],
)
async def test_append_accepts_each_public_turn_event_type(session, event_type: str) -> None:
    scope, _ = await _create_scope(session, event_type.replace(".", "-"))

    event = await append_turn_event(
        session,
        scope,
        event_type,
        {"status": "running", "step": "read_data", "deliverable_id": 7},
        event_type,
    )

    assert event.type == event_type


@pytest.mark.asyncio
async def test_append_rejects_non_public_event_type_before_allocating_sequence(session) -> None:
    scope, turn = await _create_scope(session, "private-type")

    with pytest.raises(ValueError, match="event type"):
        await append_turn_event(
            session,
            scope,
            "brain.runtime.message_delta",
            {"delta": "secret token stream"},
            "private-event",
        )

    await session.refresh(turn)
    assert turn.next_event_sequence == 1
    assert await session.scalar(select(func.count(Event.id))) == 0


@pytest.mark.asyncio
async def test_payload_uses_event_allowlist_and_recursively_removes_sensitive_fields(
    session,
) -> None:
    scope, _ = await _create_scope(session, "payload")

    event = await append_turn_event(
        session,
        scope,
        "step.started",
        {
            "step": "read_data",
            "step_key": "account-inspection:read-data",
            "status": "running",
            "progress": {
                "percent": 25,
                "token": "nested-token",
                "details": [{"summary": "safe", "password": "nested-password"}],
            },
            "metadata": {
                "source": "account_data",
                "source_id": 7,
                "retryable": True,
                "prompt": "nested prompt",
                "authorization": "Bearer secret",
                "authorization_header": "Bearer secret",
                "access_token": "access-token",
                "bearer-token": "bearer-token",
                "openai_api_key": "api-key",
                "user_prompt": "private prompt",
                "exception_stacktrace": "internal stack",
                "raw_provider_input": {"messages": ["private"]},
                "debug_context": {"trace": "internal trace"},
                "unknown_nested": "drop me",
                "nested": {"value": 1, "stack_trace": "internal stack"},
            },
            "prompt": "top-level prompt",
            "raw_model_input": {"messages": ["private"]},
            "unknown_top_level": "drop me",
        },
        "safe-payload",
    )

    assert event.payload == {
        "step": "read_data",
        "step_key": "account-inspection:read-data",
        "status": "running",
        "progress": {
            "percent": 25,
            "details": [{"summary": "safe"}],
        },
        "metadata": {
            "source": "account_data",
            "source_id": 7,
            "retryable": True,
        },
    }


@pytest.mark.asyncio
async def test_paused_payload_has_a_minimal_public_allowlist(session) -> None:
    scope, _ = await _create_scope(session, "paused-payload")

    event = await append_turn_event(
        session,
        scope,
        "turn.paused",
        {
            "status": "waiting_permission",
            "message": "Approve before publishing.",
            "turn_phase": "waiting_approval",
            "reason": "permission_required",
            "recovery_action": "approve_tool_call",
            "metadata": {"source": "tool", "prompt": "must not leak"},
            "client_message_id": "not-public-for-pause",
            "summary": "not-public-for-pause",
            "error_code": "not-public-for-pause",
            "step": "not-public-for-pause",
        },
        "paused-minimal",
    )

    assert event.payload == {
        "status": "waiting_permission",
        "message": "Approve before publishing.",
        "turn_phase": "waiting_approval",
        "reason": "permission_required",
        "recovery_action": "approve_tool_call",
        "metadata": {"source": "tool"},
    }


@pytest.mark.asyncio
async def test_steered_metadata_uses_dedicated_write_and_read_allowlist(session) -> None:
    scope, _ = await _create_scope(session, "steered-metadata")
    raw_payload = {
        "message": "已收到补充要求。",
        "metadata": {
            "category": "steering",
            "label": "supplement",
            "source_id": 88,
            "confidence": 0.99,
            "source": "internal_classifier",
            "attempt": 4,
            "prompt": "must not leak",
        },
    }

    event = await append_turn_event(
        session,
        scope,
        "turn.steered",
        raw_payload,
        "steered-dedicated-metadata",
    )

    expected = {
        "message": "已收到补充要求。",
        "metadata": {
            "category": "steering",
            "label": "supplement",
            "source_id": 88,
        },
    }
    assert event.payload == expected
    assert turn_events_service.public_turn_event_payload(
        "turn.steered",
        raw_payload,
    ) == expected


@pytest.mark.asyncio
async def test_long_idempotency_keys_are_stably_hashed_without_prefix_collisions(session) -> None:
    scope, _ = await _create_scope(session, "long-key")
    shared_prefix = "same-prefix-" + ("x" * 200)

    first = await append_turn_event(
        session,
        scope,
        "step.started",
        {"step": "read_data"},
        shared_prefix + "-one",
    )
    second = await append_turn_event(
        session,
        scope,
        "step.started",
        {"step": "read_data"},
        shared_prefix + "-two",
    )
    repeated = await append_turn_event(
        session,
        scope,
        "step.started",
        {"step": "read_data"},
        shared_prefix + "-one",
    )

    assert first.id == repeated.id
    assert first.id != second.id
    assert len(first.idempotency_key or "") == 64
    assert len(second.idempotency_key or "") == 64
    assert first.idempotency_key != second.idempotency_key


@pytest.mark.asyncio
async def test_same_scoped_business_key_cannot_be_reused_for_another_event_type(session) -> None:
    scope, turn = await _create_scope(session, "type-conflict")
    existing = await append_turn_event(
        session,
        scope,
        "step.started",
        {"step": "read_data"},
        "read-data-lifecycle",
    )

    with pytest.raises(ValueError, match="conflicts"):
        await append_turn_event(
            session,
            scope,
            "step.completed",
            {"step": "read_data"},
            "read-data-lifecycle",
        )

    await session.refresh(turn)
    assert existing.sequence == 1
    assert turn.next_event_sequence == 2
    assert await session.scalar(select(func.count(Event.id))) == 1


@pytest.mark.asyncio
async def test_unique_race_recovers_only_the_same_scoped_event_without_cursor_gap(
    session,
    monkeypatch,
) -> None:
    scope, turn = await _create_scope(session, "safe-race")
    existing = await append_turn_event(
        session,
        scope,
        "turn.received",
        {"status": "queued"},
        "received",
    )
    await session.commit()

    real_scalar = session.scalar
    hid_existing_once = False

    async def stale_scalar(statement, *args, **kwargs):
        nonlocal hid_existing_once
        descriptions = getattr(statement, "column_descriptions", ())
        entity = descriptions[0].get("entity") if descriptions else None
        if entity is Event and not hid_existing_once:
            hid_existing_once = True
            return None
        return await real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", stale_scalar)

    recovered = await append_turn_event(
        session,
        scope,
        "turn.received",
        {"status": "queued"},
        "received",
    )

    await session.refresh(turn)
    assert recovered.id == existing.id
    assert turn.next_event_sequence == 2
    assert await real_scalar(select(func.count(Event.id))) == 1


@pytest.mark.asyncio
async def test_unique_race_does_not_recover_an_event_from_another_scope(
    session,
    monkeypatch,
) -> None:
    scope_a, turn_a = await _create_scope(session, "unsafe-race-a")
    scope_b, _ = await _create_scope(session, "unsafe-race-b")
    key = turn_event_idempotency_key(scope_a, "received")
    session.add(
        Event(
            type="turn.received",
            org_id=scope_b.org_id,
            account_id=scope_b.account_id,
            thread_id=scope_b.thread_id,
            turn_id=scope_b.turn_id,
            run_id=scope_b.run_id,
            skill_run_id=scope_b.skill_run_id,
            sequence=1,
            payload={"status": "queued"},
            idempotency_key=key,
        )
    )
    await session.commit()

    real_scalar = session.scalar
    hid_existing_once = False

    async def stale_scalar(statement, *args, **kwargs):
        nonlocal hid_existing_once
        descriptions = getattr(statement, "column_descriptions", ())
        entity = descriptions[0].get("entity") if descriptions else None
        if entity is Event and not hid_existing_once:
            hid_existing_once = True
            return None
        return await real_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", stale_scalar)

    with pytest.raises(IntegrityError):
        await append_turn_event(
            session,
            scope_a,
            "turn.received",
            {"status": "queued"},
            "received",
        )

    await session.refresh(turn_a)
    assert turn_a.next_event_sequence == 1
    assert await real_scalar(select(func.count(Event.id))) == 1
