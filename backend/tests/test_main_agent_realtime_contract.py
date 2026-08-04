"""Reliability contracts for authenticated durable Main Agent events."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.turn_events import stream_authorized_thread_events
from app.models import Account, AgentRun, ConversationThread, ConversationTurn, Event, Org, User
from app.models.enums import Platform
from app.services import turn_observability
from app.services.turn_events import (
    ThreadEventScope,
    TurnEventScope,
    append_turn_event,
    list_thread_events,
)


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


class _UnavailableRedis:
    def pubsub(self):
        raise RuntimeError("test redis unavailable")


async def _scope(session, suffix: str):
    org = Org(name=f"realtime contract {suffix}")
    user = User(
        org=org,
        email=f"realtime-contract-{suffix}@example.test",
        hashed_password="not-used",
        display_name="Realtime contract operator",
    )
    account = Account(org=org, platform=Platform.DOUYIN, nickname=f"account-{suffix}")
    session.add_all([user, account])
    await session.flush()
    thread = ConversationThread(
        org_id=org.id,
        created_by_id=user.id,
        account_id=account.id,
        title=f"thread-{suffix}",
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=org.id,
        created_by_id=user.id,
        client_message_id=f"turn-{suffix}",
        user_input="Inspect the account",
    )
    session.add(turn)
    await session.flush()
    run = AgentRun(
        org_id=org.id,
        requested_by_id=user.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=f"run-{suffix}",
    )
    session.add(run)
    await session.flush()
    return account, thread, turn, TurnEventScope(
        org_id=org.id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
    )


def _metric_records(caplog, name: str):
    return [
        record
        for record in caplog.records
        if record.name == "dyflow.turn_metrics"
        and getattr(record, "metric_name", None) == name
    ]


@pytest.mark.asyncio
async def test_idempotent_append_emits_safe_publish_and_duplicate_metrics(session, caplog) -> None:
    """Removing idempotency or metric dimensions would silently duplicate UI work."""

    caplog.set_level(logging.INFO, logger="dyflow.turn_metrics")
    _account, _thread, _turn, scope = await _scope(session, "append")

    first = await append_turn_event(
        session,
        scope,
        "step.started",
        {"step": "read_data", "prompt": "must never reach telemetry"},
        "read-data",
    )
    replay = await append_turn_event(
        session,
        scope,
        "step.started",
        {"step": "read_data"},
        "read-data",
    )

    assert replay.id == first.id
    assert replay.sequence == 1
    assert await session.scalar(select(func.count(Event.id))) == 1
    publish = _metric_records(caplog, "turn_event_publish_ms")
    duplicate = _metric_records(caplog, "turn_event_duplicate_total")
    assert len(publish) == 1
    assert publish[0].metric_value >= 0
    assert publish[0].metric_dimensions == {"event_type": "step.started", "outcome": "appended"}
    assert len(duplicate) == 1
    assert duplicate[0].metric_value == 1
    assert duplicate[0].metric_dimensions == {"event_type": "step.started", "transport": "durable"}
    assert "prompt" not in publish[0].__dict__
    assert "read_data" not in str(publish[0].__dict__)


@pytest.mark.asyncio
async def test_thread_scope_excludes_foreign_account_event_from_bounded_page(session) -> None:
    """Dropping account/thread predicates would expose another account's runtime."""

    account_a, thread_a, _turn_a, scope_a = await _scope(session, "account-a")
    _account_b, _thread_b, _turn_b, scope_b = await _scope(session, "account-b")
    await append_turn_event(
        session, scope_a, "step.started", {"step": "allowed"}, "allowed"
    )
    await append_turn_event(
        session, scope_b, "step.started", {"step": "foreign"}, "foreign"
    )
    await session.commit()

    page = await list_thread_events(
        session,
        ThreadEventScope(
            org_id=scope_a.org_id,
            account_id=account_a.id,
            thread_id=thread_a.id,
        ),
        limit=500,
    )

    assert [item.payload for item in page] == [{"step": "allowed"}]
    assert [item.id for item in page] == sorted(item.id for item in page)


@pytest.mark.asyncio
async def test_recovery_delivery_gap_metrics_are_safe_and_count_each_gap_once(
    session, caplog
) -> None:
    """Removing stream recovery instrumentation would hide stalled or corrupt projections."""

    caplog.set_level(logging.INFO, logger="dyflow.turn_metrics")
    account, thread, turn, scope = await _scope(session, "stream")
    first = await append_turn_event(
        session, scope, "step.started", {"step": "one"}, "one"
    )
    await append_turn_event(
        session, scope, "step.completed", {"step": "two"}, "two"
    )
    session.add(
        Event(
            type="step.completed",
            org_id=scope.org_id,
            account_id=scope.account_id,
            thread_id=scope.thread_id,
            turn_id=scope.turn_id,
            run_id=scope.run_id,
            sequence=4,
            payload={"step": "three"},
            idempotency_key="manual-gap",
        )
    )
    await session.commit()
    maker = async_sessionmaker(session.bind, expire_on_commit=False)

    @asynccontextmanager
    async def session_factory():
        async with maker() as short_session:
            yield short_session

    stream = stream_authorized_thread_events(
        scope=ThreadEventScope(
            org_id=scope.org_id,
            account_id=account.id,
            thread_id=thread.id,
        ),
        after_id=first.id,
        request=_ConnectedRequest(),
        session_factory=session_factory,
        redis_client=_UnavailableRedis(),
        poll_seconds=60,
        heartbeat_seconds=60,
    )
    frames = [await anext(stream), await anext(stream)]
    await stream.aclose()

    assert all("foreign" not in frame for frame in frames)
    reconnect = _metric_records(caplog, "turn_stream_reconnect_total")
    delivery = _metric_records(caplog, "turn_event_delivery_lag_ms")
    gaps = _metric_records(caplog, "turn_event_sequence_gap_total")
    assert len(reconnect) == 1
    assert reconnect[0].metric_dimensions == {"transport": "sse", "reason": "resume"}
    assert len(delivery) == 2
    assert all(record.metric_value >= 0 for record in delivery)
    assert len(gaps) == 1
    assert gaps[0].metric_dimensions == {"transport": "sse", "event_type": "step.completed"}


@pytest.mark.asyncio
async def test_resume_first_event_gap_is_counted_from_cursor_seed(session, caplog) -> None:
    """The first resumed event can still reveal a durable sequence hole after the cursor."""

    caplog.set_level(logging.INFO, logger="dyflow.turn_metrics")
    account, thread, _turn, scope = await _scope(session, "first-gap")
    first = await append_turn_event(
        session, scope, "step.started", {"step": "one"}, "one"
    )
    session.add(
        Event(
            type="step.completed",
            org_id=scope.org_id,
            account_id=scope.account_id,
            thread_id=scope.thread_id,
            turn_id=scope.turn_id,
            run_id=scope.run_id,
            sequence=4,
            payload={"step": "after-gap"},
            idempotency_key="after-gap",
        )
    )
    await session.commit()
    maker = async_sessionmaker(session.bind, expire_on_commit=False)

    @asynccontextmanager
    async def session_factory():
        async with maker() as short_session:
            yield short_session

    stream = stream_authorized_thread_events(
        scope=ThreadEventScope(
            org_id=scope.org_id,
            account_id=account.id,
            thread_id=thread.id,
        ),
        after_id=first.id,
        request=_ConnectedRequest(),
        session_factory=session_factory,
        redis_client=_UnavailableRedis(),
        poll_seconds=60,
        heartbeat_seconds=60,
    )
    frame = await anext(stream)
    await stream.aclose()

    assert "after-gap" in frame
    gaps = _metric_records(caplog, "turn_event_sequence_gap_total")
    assert len(gaps) == 1
    assert gaps[0].metric_dimensions == {"transport": "sse", "event_type": "step.completed"}


@pytest.mark.asyncio
async def test_telemetry_failure_never_aborts_durable_append_or_delivery(
    session, monkeypatch
) -> None:
    """A failed log sink must not turn an otherwise durable turn into a user failure."""

    account, thread, _turn, scope = await _scope(session, "sink-failure")

    def raise_sink(*_args, **_kwargs):
        raise RuntimeError("metrics sink unavailable")

    monkeypatch.setattr(turn_observability.METRICS_LOGGER, "info", raise_sink)
    event = await append_turn_event(
        session, scope, "step.started", {"step": "still-durable"}, "still-durable"
    )
    await session.commit()

    assert event.id is not None
    assert await session.scalar(select(func.count(Event.id))) == 1

    maker = async_sessionmaker(session.bind, expire_on_commit=False)

    @asynccontextmanager
    async def session_factory():
        async with maker() as short_session:
            yield short_session

    stream = stream_authorized_thread_events(
        scope=ThreadEventScope(
            org_id=scope.org_id,
            account_id=account.id,
            thread_id=thread.id,
        ),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=session_factory,
        redis_client=_UnavailableRedis(),
        poll_seconds=60,
        heartbeat_seconds=60,
    )
    frame = await anext(stream)
    await stream.aclose()

    assert f"id: {event.id}" in frame
