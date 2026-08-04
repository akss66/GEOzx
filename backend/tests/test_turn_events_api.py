"""Authenticated incremental recovery and SSE contracts for Turn events."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import turn_events as turn_events_api
from app.core.security import create_access_token, hash_password
from app.db import Base
from app.models import (
    Account,
    AgentRun,
    Client,
    ClientMembership,
    ConversationThread,
    ConversationTurn,
    Event,
    Org,
    User,
)
from app.models.enums import Platform, UserRole, WorkspaceRole
from app.services.turn_events import ThreadEventScope, TurnEventScope, append_turn_event


def _auth(user) -> dict[str, str]:
    token = create_access_token(str(user.id), user.role.value)
    return {"Authorization": f"Bearer {token}"}


async def _event_scope(session, user, *, key: str):
    account = Account(
        org_id=user.org_id,
        platform=Platform.DOUYIN,
        nickname=f"account-{key}",
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=user.org_id,
        created_by_id=user.id,
        account_id=account.id,
        title=key,
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=user.org_id,
        created_by_id=user.id,
        client_message_id=key,
        user_input=key,
    )
    session.add(turn)
    await session.flush()
    run = AgentRun(
        org_id=user.org_id,
        requested_by_id=user.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=key,
    )
    session.add(run)
    await session.commit()
    return account, thread, turn, run, TurnEventScope(
        org_id=user.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
    )


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


class _FakePubSub:
    def __init__(self, messages: list[dict] | None = None, tracker=None) -> None:
        self.messages = list(messages or [])
        self.tracker = tracker
        self.subscribed = False
        self.unsubscribed = False
        self.closed = False
        self.waiting = asyncio.Event()
        self.message_delivered = asyncio.Event()

    async def subscribe(self, _channel: str) -> None:
        self.subscribed = True

    async def get_message(
        self, *, ignore_subscribe_messages: bool, timeout: float  # noqa: ASYNC109
    ):
        assert ignore_subscribe_messages is True
        if self.tracker is not None:
            assert self.tracker.active == 0
        self.waiting.set()
        if self.messages:
            self.message_delivered.set()
            return self.messages.pop(0)
        await asyncio.sleep(timeout)
        return None

    async def unsubscribe(self, _channel: str) -> None:
        self.unsubscribed = True

    async def aclose(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self, pubsub: _FakePubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> _FakePubSub:
        return self._pubsub


class _FactoryFailRedis:
    def pubsub(self):
        raise RuntimeError("redis pubsub factory failed")


class _SubscribeFailPubSub(_FakePubSub):
    async def subscribe(self, _channel: str) -> None:
        raise RuntimeError("redis unavailable during subscribe")


class _WaitFailPubSub(_FakePubSub):
    async def get_message(
        self, *, ignore_subscribe_messages: bool, timeout: float  # noqa: ASYNC109
    ):
        assert ignore_subscribe_messages is True
        if self.tracker is not None:
            assert self.tracker.active == 0
        self.waiting.set()
        raise RuntimeError("redis unavailable during wait")


class _CleanupFailPubSub(_FakePubSub):
    async def unsubscribe(self, _channel: str) -> None:
        self.unsubscribed = True
        raise RuntimeError("redis unsubscribe failed")

    async def aclose(self) -> None:
        self.closed = True
        raise RuntimeError("redis close failed")


def _stream_scope(account, thread) -> ThreadEventScope:
    return ThreadEventScope(
        org_id=thread.org_id,
        account_id=account.id,
        thread_id=thread.id,
    )


def _stream_generator(**kwargs):
    factory = getattr(turn_events_api, "stream_authorized_thread_events", None)
    assert callable(factory), "authenticated SSE generator is not implemented"
    return factory(**kwargs)


class _SessionTracker:
    def __init__(self) -> None:
        self.active = 0
        self.opened = 0
        self.closed = 0


def _tracked_session_factory(session, tracker: _SessionTracker):
    maker = async_sessionmaker(session.bind, expire_on_commit=False)

    @asynccontextmanager
    async def factory():
        tracker.active += 1
        tracker.opened += 1
        try:
            async with maker() as short_session:
                yield short_session
        finally:
            tracker.active -= 1
            tracker.closed += 1

    return factory


def _tracked_maker_factory(maker, tracker: _SessionTracker):
    @asynccontextmanager
    async def factory():
        tracker.active += 1
        tracker.opened += 1
        try:
            async with maker() as short_session:
                yield short_session
        finally:
            tracker.active -= 1
            tracker.closed += 1

    return factory


@pytest_asyncio.fixture
async def concurrent_event_db(tmp_path):
    database_path = (tmp_path / "turn-events.sqlite3").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as writer:
        org = Org(name="concurrent event org")
        user = User(
            org=org,
            email="event-stream@test.com",
            hashed_password=hash_password("event-stream-password"),
            display_name="event stream user",
            role=UserRole.ADMIN,
        )
        writer.add(user)
        await writer.commit()
        await writer.refresh(user)
        yield writer, user, maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_turn_event_routes_require_bearer_authentication(client) -> None:
    listing = await client.get("/conversation-threads/1/events")
    stream = await client.get("/conversation-threads/1/event-stream")

    assert listing.status_code == 401
    assert stream.status_code == 401


@pytest.mark.asyncio
async def test_query_string_token_cannot_replace_bearer_header(client, admin) -> None:
    token = create_access_token(str(admin.id), admin.role.value)

    response = await client.get(
        f"/conversation-threads/1/events?access_token={token}"
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_turn_events_returns_only_ids_after_cursor_in_ascending_order(
    client,
    session,
    admin,
) -> None:
    _account, thread, turn, run, scope = await _event_scope(
        session, admin, key="incremental-list"
    )
    first = await append_turn_event(
        session,
        scope,
        "turn.received",
        {"status": "queued", "client_message_id": run.client_message_id},
        "received",
    )
    second = await append_turn_event(
        session,
        scope,
        "step.started",
        {
            "step": "read_data",
            "status": "started",
            "metadata": {"attempt": 1},
            "raw_prompt": "must never be public",
        },
        "step:read_data:attempt:1:started",
    )
    await session.commit()

    response = await client.get(
        f"/conversation-threads/{thread.id}/events?after_id={first.id}",
        headers=_auth(admin),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json() == {
        "data": [
            {
                "id": second.id,
                "sequence": 2,
                "type": "step.started",
                "payload": {
                    "step": "read_data",
                    "status": "started",
                    "metadata": {"attempt": 1},
                },
                "thread_id": thread.id,
                "turn_id": turn.id,
                "run_id": run.id,
                "skill_run_id": None,
                "created_at": second.created_at.isoformat().replace("+00:00", "Z"),
            }
        ]
    }


@pytest.mark.asyncio
async def test_same_org_non_owner_cannot_read_another_users_thread_events(
    client,
    session,
    admin,
    member,
) -> None:
    _account, thread, _turn, _run, scope = await _event_scope(
        session, admin, key="same-org-owner-boundary"
    )
    await append_turn_event(
        session,
        scope,
        "turn.received",
        {"status": "queued"},
        "received",
    )
    await session.commit()

    responses = [
        await client.get(
            f"/conversation-threads/{thread.id}/{endpoint}",
            headers=_auth(member),
        )
        for endpoint in ("events", "event-stream")
    ]

    assert [response.status_code for response in responses] == [404, 404]


@pytest.mark.asyncio
async def test_thread_owner_with_revoked_account_access_receives_404(
    client,
    session,
    admin,
    member,
) -> None:
    workspace = Client(org_id=admin.org_id, name="revoked-event-workspace")
    account = Account(
        org_id=admin.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="revoked-event-account",
    )
    membership = ClientMembership(
        client=workspace,
        user=member,
        role=WorkspaceRole.OPERATOR,
    )
    session.add_all([workspace, account, membership])
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=member.id,
        client_id=workspace.id,
        account_id=account.id,
        title="revoked-owner-thread",
    )
    session.add(thread)
    await session.commit()
    await session.execute(
        delete(ClientMembership).where(ClientMembership.id == membership.id)
    )
    await session.commit()

    responses = [
        await client.get(
            f"/conversation-threads/{thread.id}/{endpoint}",
            headers=_auth(member),
        )
        for endpoint in ("events", "event-stream")
    ]

    assert [response.status_code for response in responses] == [404, 404]


@pytest.mark.asyncio
async def test_other_organization_cannot_enumerate_thread_events(
    client,
    session,
    admin,
) -> None:
    _account, thread, _turn, _run, _scope = await _event_scope(
        session, admin, key="cross-org-boundary"
    )
    foreign_org = Org(name="foreign-event-org")
    foreign_user = User(
        org=foreign_org,
        email="foreign-turn-events@example.com",
        hashed_password=hash_password("not-used"),
        display_name="Foreign event user",
        role=UserRole.ADMIN,
    )
    session.add(foreign_user)
    await session.commit()

    responses = [
        await client.get(
            f"/conversation-threads/{thread.id}/{endpoint}",
            headers=_auth(foreign_user),
        )
        for endpoint in ("events", "event-stream")
    ]

    assert [response.status_code for response in responses] == [404, 404]


@pytest.mark.asyncio
async def test_list_filters_foreign_account_and_thread_rows_even_if_lineage_is_forged(
    client,
    session,
    admin,
) -> None:
    account, thread, turn, run, scope = await _event_scope(
        session, admin, key="query-scope-filter"
    )
    other_account, other_thread, other_turn, other_run, _other_scope = await _event_scope(
        session, admin, key="query-scope-filter-other"
    )
    visible = await append_turn_event(
        session,
        scope,
        "turn.received",
        {"status": "queued"},
        "received",
    )
    session.add_all(
        [
            Event(
                type="step.started",
                org_id=admin.org_id,
                account_id=other_account.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                sequence=2,
                payload={"step": "foreign_account"},
                idempotency_key="foreign-account-event",
            ),
            Event(
                type="step.started",
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=other_thread.id,
                turn_id=other_turn.id,
                run_id=other_run.id,
                sequence=1,
                payload={"step": "foreign_thread"},
                idempotency_key="foreign-thread-event",
            ),
        ]
    )
    await session.commit()

    response = await client.get(
        f"/conversation-threads/{thread.id}/events",
        headers=_auth(admin),
    )

    assert response.status_code == 200
    assert [event["id"] for event in response.json()["data"]] == [visible.id]


@pytest.mark.asyncio
async def test_list_sanitizes_legacy_public_type_rows_again_at_read_boundary(
    client,
    session,
    admin,
) -> None:
    account, thread, turn, run, _scope = await _event_scope(
        session, admin, key="read-boundary-sanitize"
    )
    raw = Event(
        type="step.failed",
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        sequence=1,
        payload={
            "step": "quality_review",
            "status": "failed",
            "error_code": "MODEL_FAILED",
            "raw_prompt": "secret prompt",
            "exception_stack": "private traceback",
            "metadata": {
                "attempt": 2,
                "raw_model_input": "private input",
                "internal_trace": "private trace",
            },
        },
        idempotency_key="raw-legacy-public-row",
    )
    session.add(raw)
    await session.commit()

    response = await client.get(
        f"/conversation-threads/{thread.id}/events",
        headers=_auth(admin),
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["payload"] == {
        "step": "quality_review",
        "status": "failed",
        "error_code": "MODEL_FAILED",
        "metadata": {"attempt": 2},
    }


@pytest.mark.asyncio
async def test_list_never_exposes_revision_hashes_or_snapshots(
    client,
    session,
    admin,
) -> None:
    account, thread, turn, run, _scope = await _event_scope(
        session, admin, key="revision-list-sanitize"
    )
    session.add(
        Event(
            type="run.revision_planned",
            org_id=admin.org_id,
            account_id=account.id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            sequence=1,
            payload={
                "revision_id": 41,
                "revision_run_id": run.id,
                "status": "planned",
                "plan_hash": "private-plan-hash",
                "input_snapshot": {"secret": "input"},
                "output_snapshot": {"secret": "output"},
                "snapshot_hash": "private-snapshot-hash",
            },
            idempotency_key="revision-list-sanitize",
        )
    )
    await session.commit()

    response = await client.get(
        f"/conversation-threads/{thread.id}/events",
        headers=_auth(admin),
    )

    assert response.status_code == 200
    assert response.json()["data"][0]["payload"] == {
        "revision_id": 41,
        "revision_run_id": run.id,
        "status": "planned",
    }


@pytest.mark.asyncio
async def test_list_drops_corrupt_nested_payload_and_continues_recovery(
    client,
    session,
    admin,
) -> None:
    account, thread, turn, run, _scope = await _event_scope(
        session, admin, key="list-corrupt-nested-payload"
    )
    corrupt = Event(
        type="step.started",
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        sequence=1,
        payload={
            "step": "corrupt",
            "status": "started",
            "metadata": "legacy-corrupt",
            "raw_prompt": "must not leak",
        },
        idempotency_key="list-corrupt-nested-payload",
    )
    visible = Event(
        type="step.started",
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        sequence=2,
        payload={"step": "valid", "status": "started"},
        idempotency_key="list-valid-after-corrupt-payload",
    )
    session.add_all([corrupt, visible])
    await session.commit()

    response = await client.get(
        f"/conversation-threads/{thread.id}/events",
        headers=_auth(admin),
    )

    assert response.status_code == 200
    assert [event["payload"] for event in response.json()["data"]] == [
        {"step": "corrupt", "status": "started"},
        {"step": "valid", "status": "started"},
    ]


@pytest.mark.asyncio
async def test_stream_drops_corrupt_nested_payload_and_continues_recovery(
    session,
    admin,
) -> None:
    account, thread, turn, run, _scope = await _event_scope(
        session, admin, key="stream-corrupt-nested-payload"
    )
    session.add_all(
        [
            Event(
                type="step.started",
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                sequence=1,
                payload={
                    "step": "corrupt",
                    "status": "started",
                    "metadata": "legacy-corrupt",
                    "exception_stack": "must not leak",
                },
                idempotency_key="stream-corrupt-nested-payload",
            ),
            Event(
                type="step.started",
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                sequence=2,
                payload={"step": "valid", "status": "started"},
                idempotency_key="stream-valid-after-corrupt-payload",
            ),
        ]
    )
    await session.commit()
    tracker = _SessionTracker()
    pubsub = _FakePubSub(tracker=tracker)
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=_tracked_session_factory(session, tracker),
        redis_client=_FakeRedis(pubsub),
        poll_seconds=60,
        heartbeat_seconds=60,
    )

    frames = [await anext(stream), await anext(stream)]
    await stream.aclose()

    payloads = [
        json.loads(frame.splitlines()[2].removeprefix("data: "))["payload"]
        for frame in frames
    ]
    assert payloads == [
        {"step": "corrupt", "status": "started"},
        {"step": "valid", "status": "started"},
    ]


@pytest.mark.asyncio
async def test_stream_never_exposes_revision_hashes_or_snapshots(session, admin) -> None:
    account, thread, turn, run, _scope = await _event_scope(
        session, admin, key="revision-stream-sanitize"
    )
    session.add(
        Event(
            type="run.revision_fallback",
            org_id=admin.org_id,
            account_id=account.id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            sequence=1,
            payload={
                "revision_id": 42,
                "revision_run_id": run.id,
                "status": "planned",
                "reason": "missing_executor_boundary",
                "plan_hash": "private-plan-hash",
                "input_snapshot": {"secret": "input"},
                "output_snapshot": {"secret": "output"},
                "snapshot_hash": "private-snapshot-hash",
            },
            idempotency_key="revision-stream-sanitize",
        )
    )
    await session.commit()
    tracker = _SessionTracker()
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=_tracked_session_factory(session, tracker),
        redis_client=_FakeRedis(_FakePubSub(tracker=tracker)),
        poll_seconds=60,
        heartbeat_seconds=60,
    )

    frame = await anext(stream)
    await stream.aclose()

    payload = json.loads(frame.splitlines()[2].removeprefix("data: "))["payload"]
    assert payload == {
        "revision_id": 42,
        "revision_run_id": run.id,
        "status": "planned",
        "reason": "missing_executor_boundary",
    }


@pytest.mark.asyncio
async def test_list_contains_bad_legacy_rows_and_returns_later_valid_event_safely(
    client,
    session,
    admin,
) -> None:
    account, thread, turn, run, scope = await _event_scope(
        session, admin, key="read-boundary-incomplete"
    )
    session.add_all(
        [
            Event(
                type="step.started",
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                sequence=None,
                payload={"step": "missing_sequence", "status": "started"},
                idempotency_key="legacy-missing-sequence",
            ),
            Event(
                type="step.started",
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=None,
                run_id=run.id,
                sequence=99,
                payload={"step": "missing_turn", "status": "started"},
                idempotency_key="legacy-missing-turn",
            ),
            Event(
                type="step.started",
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                sequence=0,
                payload={"step": "non_positive_sequence", "status": "started"},
                idempotency_key="legacy-non-positive-sequence",
            ),
            Event(
                type="step.started",
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                sequence=2,
                payload=None,
                idempotency_key="legacy-null-payload",
            ),
            Event(
                type="step.started",
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                sequence=3,
                payload=["private non-object payload"],
                idempotency_key="legacy-list-payload",
            ),
        ]
    )
    await session.commit()
    visible = await append_turn_event(
        session,
        scope,
        "step.started",
        {"step": "valid", "status": "started"},
        "step:valid:attempt:1:started",
    )
    await session.commit()

    response = await client.get(
        f"/conversation-threads/{thread.id}/events",
        headers=_auth(admin),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data[-1]["id"] == visible.id
    assert [event["payload"] for event in data[:-1]] == [{}, {}]


@pytest.mark.asyncio
@pytest.mark.parametrize("after_id", ["-1", "not-an-integer", "1.5"])
async def test_list_rejects_negative_or_invalid_after_id(
    client,
    session,
    admin,
    after_id,
) -> None:
    _account, thread, _turn, _run, _scope = await _event_scope(
        session, admin, key=f"invalid-after-{after_id}"
    )

    response = await client.get(
        f"/conversation-threads/{thread.id}/events?after_id={after_id}",
        headers=_auth(admin),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_caps_each_page_at_500_and_continues_after_last_id(
    client,
    session,
    admin,
) -> None:
    account, thread, turn, run, _scope = await _event_scope(
        session, admin, key="bounded-list-page"
    )
    session.add_all(
        [
            Event(
                type="step.started",
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
                sequence=index,
                payload={"step": "read_data", "status": "started"},
                idempotency_key=f"bounded-list-{index}",
            )
            for index in range(1, 502)
        ]
    )
    await session.commit()

    first_page = await client.get(
        f"/conversation-threads/{thread.id}/events",
        headers=_auth(admin),
    )
    first_data = first_page.json()["data"]
    second_page = await client.get(
        f"/conversation-threads/{thread.id}/events?after_id={first_data[-1]['id']}",
        headers=_auth(admin),
    )

    assert first_page.status_code == 200
    assert len(first_data) == 500
    assert [item["id"] for item in first_data] == sorted(item["id"] for item in first_data)
    assert second_page.status_code == 200
    assert len(second_page.json()["data"]) == 1


@pytest.mark.asyncio
async def test_stream_response_has_sse_headers_and_releases_authorization_session(
    monkeypatch,
) -> None:
    class AuthorizationSession:
        def __init__(self) -> None:
            self.rolled_back = False
            self.closed = False

        async def rollback(self) -> None:
            self.rolled_back = True

        async def close(self) -> None:
            self.closed = True

    async def authorize(_session, _user, thread_id):
        return SimpleNamespace(id=thread_id, account_id=23)

    authorization_session = AuthorizationSession()
    monkeypatch.setattr(turn_events_api, "get_conversation_thread", authorize)

    response = await turn_events_api.stream_conversation_turn_events(
        thread_id=17,
        user=SimpleNamespace(org_id=11),
        session=authorization_session,
        request=_ConnectedRequest(),
        after_id=0,
    )

    assert response.status_code == 200
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert authorization_session.rolled_back is True
    assert authorization_session.closed is True
    await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_stream_http_response_preserves_sse_cache_headers_through_middleware(
    client,
    session,
    admin,
    monkeypatch,
) -> None:
    _account, thread, _turn, _run, _scope = await _event_scope(
        session, admin, key="stream-http-headers"
    )

    async def finite_stream(**_kwargs):
        yield ": heartbeat\n\n"

    monkeypatch.setattr(
        turn_events_api,
        "stream_authorized_thread_events",
        finite_stream,
        raising=False,
    )

    response = await client.get(
        f"/conversation-threads/{thread.id}/event-stream",
        headers=_auth(admin),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text == ": heartbeat\n\n"


@pytest.mark.asyncio
async def test_stream_backfills_database_gap_with_sse_id_event_and_json_data(
    session,
    admin,
) -> None:
    account, thread, _turn, _run, event_scope = await _event_scope(
        session, admin, key="stream-backfill"
    )
    first = await append_turn_event(
        session,
        event_scope,
        "turn.received",
        {"status": "queued"},
        "received",
    )
    second = await append_turn_event(
        session,
        event_scope,
        "step.started",
        {"step": "read_data", "status": "started"},
        "step:read_data:attempt:1:started",
    )
    await session.commit()
    first_id = first.id
    second_id = second.id
    tracker = _SessionTracker()
    pubsub = _FakePubSub(tracker=tracker)
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=first_id,
        request=_ConnectedRequest(),
        session_factory=_tracked_session_factory(session, tracker),
        redis_client=_FakeRedis(pubsub),
        poll_seconds=0.01,
        heartbeat_seconds=1,
    )

    frame = await asyncio.wait_for(anext(stream), timeout=0.5)
    await stream.aclose()

    lines = frame.strip().splitlines()
    assert lines[0] == f"id: {second_id}"
    assert lines[1] == "event: step.started"
    data = json.loads(lines[2].removeprefix("data: "))
    assert data["id"] == second_id
    assert data["payload"] == {"step": "read_data", "status": "started"}
    assert tracker.active == 0
    assert tracker.opened == tracker.closed
    assert pubsub.unsubscribed is True
    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_stream_drains_more_than_500_backlog_events_before_waiting(
    session,
    admin,
) -> None:
    account, thread, turn, run, _scope = await _event_scope(
        session, admin, key="stream-bounded-backlog"
    )
    events = [
        Event(
            type="step.started",
            org_id=admin.org_id,
            account_id=account.id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            sequence=index,
            payload={"step": f"backlog_{index}", "status": "started"},
            idempotency_key=f"stream-bounded-backlog-{index}",
        )
        for index in range(1, 502)
    ]
    session.add_all(events)
    await session.commit()
    expected_ids = [event.id for event in events]
    tracker = _SessionTracker()
    pubsub = _FakePubSub(tracker=tracker)
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=_tracked_session_factory(session, tracker),
        redis_client=_FakeRedis(pubsub),
        poll_seconds=60,
        heartbeat_seconds=60,
    )

    frames = [await anext(stream) for _ in range(501)]
    await stream.aclose()

    assert [int(frame.splitlines()[0].removeprefix("id: ")) for frame in frames] == expected_ids
    assert pubsub.waiting.is_set() is False
    assert tracker.active == 0
    assert tracker.opened == tracker.closed == 2


@pytest.mark.asyncio
async def test_stream_sends_heartbeat_comment_without_database_events(
    session,
    admin,
) -> None:
    account, thread, _turn, _run, _scope = await _event_scope(
        session, admin, key="stream-heartbeat"
    )
    tracker = _SessionTracker()
    pubsub = _FakePubSub(tracker=tracker)
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=_tracked_session_factory(session, tracker),
        redis_client=_FakeRedis(pubsub),
        poll_seconds=0.01,
        heartbeat_seconds=0.03,
    )

    frame = await asyncio.wait_for(anext(stream), timeout=0.5)
    await stream.aclose()

    assert frame == ": heartbeat\n\n"
    assert tracker.active == 0
    assert tracker.opened == tracker.closed


@pytest.mark.asyncio
async def test_stream_poll_fallback_discovers_commit_without_redis_message(
    concurrent_event_db,
) -> None:
    session, admin, maker = concurrent_event_db
    account, thread, _turn, _run, event_scope = await _event_scope(
        session, admin, key="stream-poll-fallback"
    )
    tracker = _SessionTracker()
    pubsub = _FakePubSub(tracker=tracker)
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=_tracked_maker_factory(maker, tracker),
        redis_client=_FakeRedis(pubsub),
        poll_seconds=0.01,
        heartbeat_seconds=1,
    )
    next_frame = asyncio.create_task(anext(stream))
    await asyncio.wait_for(pubsub.waiting.wait(), timeout=0.2)
    event = await append_turn_event(
        session,
        event_scope,
        "step.started",
        {"step": "read_data", "status": "started"},
        "step:read_data:attempt:1:started",
    )
    await session.commit()
    event_id = event.id

    frame = await asyncio.wait_for(next_frame, timeout=0.5)
    await stream.aclose()

    assert frame.startswith(f"id: {event_id}\n")
    assert pubsub.message_delivered.is_set() is False
    assert tracker.active == 0


@pytest.mark.asyncio
async def test_stream_uses_database_when_redis_pubsub_factory_raises_runtime_error(
    session,
    admin,
) -> None:
    account, thread, _turn, _run, event_scope = await _event_scope(
        session, admin, key="stream-pubsub-factory-failure"
    )
    event = await append_turn_event(
        session,
        event_scope,
        "step.started",
        {"step": "database_only", "status": "started"},
        "step:database_only:attempt:1:started",
    )
    await session.commit()
    event_id = event.id
    tracker = _SessionTracker()
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=_tracked_session_factory(session, tracker),
        redis_client=_FactoryFailRedis(),
        poll_seconds=0.01,
        heartbeat_seconds=1,
    )

    frame = await asyncio.wait_for(anext(stream), timeout=0.5)
    await stream.aclose()

    assert frame.startswith(f"id: {event_id}\n")


@pytest.mark.asyncio
async def test_stream_uses_database_when_redis_subscribe_fails(
    session,
    admin,
) -> None:
    account, thread, _turn, _run, event_scope = await _event_scope(
        session, admin, key="stream-subscribe-failure"
    )
    event = await append_turn_event(
        session,
        event_scope,
        "step.started",
        {"step": "database_only", "status": "started"},
        "step:database_only:attempt:1:started",
    )
    await session.commit()
    event_id = event.id
    tracker = _SessionTracker()
    pubsub = _SubscribeFailPubSub(tracker=tracker)
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=_tracked_session_factory(session, tracker),
        redis_client=_FakeRedis(pubsub),
        poll_seconds=0.01,
        heartbeat_seconds=1,
    )

    frame = await asyncio.wait_for(anext(stream), timeout=0.5)
    await stream.aclose()

    assert frame.startswith(f"id: {event_id}\n")
    assert pubsub.unsubscribed is False
    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_redis_cleanup_runtime_errors_do_not_escape_generator_close(
    session,
    admin,
) -> None:
    account, thread, _turn, _run, event_scope = await _event_scope(
        session, admin, key="stream-cleanup-generator-close"
    )
    await append_turn_event(
        session,
        event_scope,
        "step.started",
        {"step": "cleanup", "status": "started"},
        "step:cleanup:attempt:1:started",
    )
    await session.commit()
    tracker = _SessionTracker()
    pubsub = _CleanupFailPubSub(tracker=tracker)
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=_tracked_session_factory(session, tracker),
        redis_client=_FakeRedis(pubsub),
        poll_seconds=60,
        heartbeat_seconds=60,
    )

    await anext(stream)
    await stream.aclose()

    assert pubsub.unsubscribed is True
    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_stream_uses_database_polling_after_redis_wait_fails(
    concurrent_event_db,
) -> None:
    session, admin, maker = concurrent_event_db
    account, thread, _turn, _run, event_scope = await _event_scope(
        session, admin, key="stream-wait-failure"
    )
    tracker = _SessionTracker()
    pubsub = _WaitFailPubSub(tracker=tracker)
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=_tracked_maker_factory(maker, tracker),
        redis_client=_FakeRedis(pubsub),
        poll_seconds=0.01,
        heartbeat_seconds=1,
    )
    next_frame = asyncio.create_task(anext(stream))
    await asyncio.wait_for(pubsub.waiting.wait(), timeout=0.2)
    event = await append_turn_event(
        session,
        event_scope,
        "step.started",
        {"step": "database_after_redis_failure", "status": "started"},
        "step:database_after_redis_failure:attempt:1:started",
    )
    await session.commit()
    event_id = event.id

    frame = await asyncio.wait_for(next_frame, timeout=0.5)
    await stream.aclose()

    assert frame.startswith(f"id: {event_id}\n")
    assert pubsub.unsubscribed is True
    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_wrong_redis_wakeup_never_forwards_foreign_payload(
    concurrent_event_db,
) -> None:
    session, admin, maker = concurrent_event_db
    account, thread, _turn, _run, event_scope = await _event_scope(
        session, admin, key="stream-authorized-wakeup"
    )
    other_account, other_thread, _other_turn, _other_run, other_scope = await _event_scope(
        session, admin, key="stream-foreign-wakeup"
    )
    foreign = await append_turn_event(
        session,
        other_scope,
        "step.started",
        {"step": "foreign_secret", "status": "started"},
        "step:foreign_secret:attempt:1:started",
    )
    await session.commit()
    tracker = _SessionTracker()
    pubsub = _FakePubSub(
        messages=[
            {
                "type": "message",
                "data": json.dumps(
                    {
                        "id": foreign.id,
                        "org_id": other_thread.org_id,
                        "account_id": other_account.id,
                        "thread_id": other_thread.id,
                        "payload": {"step": "foreign_secret"},
                    }
                ),
            }
        ],
        tracker=tracker,
    )
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=_tracked_maker_factory(maker, tracker),
        redis_client=_FakeRedis(pubsub),
        poll_seconds=0.01,
        heartbeat_seconds=1,
    )
    next_frame = asyncio.create_task(anext(stream))
    await asyncio.wait_for(pubsub.message_delivered.wait(), timeout=0.2)
    visible = await append_turn_event(
        session,
        event_scope,
        "step.started",
        {"step": "authorized", "status": "started"},
        "step:authorized:attempt:1:started",
    )
    await session.commit()
    visible_id = visible.id

    frame = await asyncio.wait_for(next_frame, timeout=0.5)
    await stream.aclose()

    assert frame.startswith(f"id: {visible_id}\n")
    assert "foreign_secret" not in frame


@pytest.mark.asyncio
async def test_stream_cancellation_cleans_subscription_and_propagates_cancelled_error(
    session,
    admin,
) -> None:
    account, thread, _turn, _run, _scope = await _event_scope(
        session, admin, key="stream-cancel-cleanup"
    )
    tracker = _SessionTracker()
    pubsub = _CleanupFailPubSub(tracker=tracker)
    stream = _stream_generator(
        scope=_stream_scope(account, thread),
        after_id=0,
        request=_ConnectedRequest(),
        session_factory=_tracked_session_factory(session, tracker),
        redis_client=_FakeRedis(pubsub),
        poll_seconds=60,
        heartbeat_seconds=60,
    )
    next_frame = asyncio.create_task(anext(stream))
    await asyncio.wait_for(pubsub.waiting.wait(), timeout=0.2)

    next_frame.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_frame

    assert next_frame.done()
    assert tracker.active == 0
    assert tracker.opened == tracker.closed
    assert pubsub.unsubscribed is True
    assert pubsub.closed is True
