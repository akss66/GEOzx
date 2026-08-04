from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import ws as ws_api


class _FakePubSub:
    def __init__(self, messages: list[str] | None = None) -> None:
        self.unsubscribed = False
        self.closed = False
        self.messages = messages or ["payload"]

    async def subscribe(self, _channel: str) -> None:
        return None

    async def listen(self):
        for data in self.messages:
            yield {"type": "message", "data": data}

    async def unsubscribe(self, _channel: str) -> None:
        self.unsubscribed = True

    async def aclose(self) -> None:
        self.closed = True


class _FakeRedis:
    def __init__(self, pubsub: _FakePubSub) -> None:
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self) -> _FakePubSub:
        return self._pubsub

    async def aclose(self) -> None:
        self.closed = True


class _ClosedTransportWebSocket:
    def __init__(self) -> None:
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, _data: str) -> None:
        raise RuntimeError(
            "unable to perform operation on <TCPTransport closed=True>; the handler is closed"
        )


class _RecordingWebSocket(_ClosedTransportWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


class _HandshakeWebSocket(_RecordingWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.closed_code: int | None = None

    async def close(self, code: int) -> None:
        self.closed_code = code

    async def receive_json(self):
        return {"type": "authenticate", "token": "token", "thread_id": 81}


@pytest.mark.asyncio
async def test_ws_events_treats_closed_transport_as_normal_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pubsub = _FakePubSub()
    redis = _FakeRedis(pubsub)
    websocket = _ClosedTransportWebSocket()

    monkeypatch.setattr(
        ws_api.aioredis,
        "from_url",
        lambda *_args, **_kwargs: redis,
    )

    await ws_api.ws_events(websocket)  # type: ignore[arg-type]

    assert websocket.accepted is True
    assert pubsub.unsubscribed is True
    assert pubsub.closed is True
    assert redis.closed is True


@pytest.mark.asyncio
async def test_ws_events_does_not_hide_unrelated_runtime_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pubsub = _FakePubSub()
    redis = _FakeRedis(pubsub)

    class _BrokenWebSocket(_ClosedTransportWebSocket):
        async def send_text(self, _data: str) -> None:
            raise RuntimeError("unexpected websocket failure")

    monkeypatch.setattr(
        ws_api.aioredis,
        "from_url",
        lambda *_args, **_kwargs: redis,
    )

    with pytest.raises(RuntimeError, match="unexpected websocket failure"):
        await ws_api.ws_events(_BrokenWebSocket())  # type: ignore[arg-type]

    assert pubsub.unsubscribed is True
    assert pubsub.closed is True
    assert redis.closed is True


@pytest.mark.asyncio
async def test_ws_events_filters_scoped_or_public_turn_messages_but_keeps_legacy_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_runtime = '{"type":"brain.runtime.message_delta","payload":{"thread_id":81}}'
    public_turn = '{"id":1,"type":"step.started","thread_id":81,"turn_id":101}'
    legacy_event = '{"type":"content.updated","payload":{"content_item_id":3}}'
    pubsub = _FakePubSub([private_runtime, public_turn, legacy_event])
    redis = _FakeRedis(pubsub)
    websocket = _RecordingWebSocket()
    monkeypatch.setattr(ws_api.aioredis, "from_url", lambda *_args, **_kwargs: redis)

    await ws_api.ws_events(websocket)  # type: ignore[arg-type]

    assert websocket.sent == [legacy_event]


def test_thread_runtime_filter_only_admits_the_scoped_ephemeral_message_events() -> None:
    allowed = (
        '{"type":"brain.runtime.message_delta",'
        '"payload":{"thread_id":81,"turn_id":101,"delta":"a"}}'
    )
    wrong_thread = '{"type":"brain.runtime.message_delta","payload":{"thread_id":82,"turn_id":101}}'
    durable = '{"type":"turn.completed","thread_id":81,"turn_id":101,"payload":{}}'

    assert ws_api._runtime_event_for_thread(allowed, 81) is True
    assert ws_api._runtime_event_for_thread(wrong_thread, 81) is False
    assert ws_api._runtime_event_for_thread(durable, 81) is False


def test_legacy_filter_defaults_safe_for_malformed_but_obviously_private_turn_frames() -> None:
    assert ws_api._should_forward_legacy_event('{"type":"turn.completed"') is False
    assert ws_api._should_forward_legacy_event('{"type":"step.started"') is False
    assert ws_api._should_forward_legacy_event('{"type":"content.updated"') is True


@pytest.mark.asyncio
async def test_thread_runtime_rejects_an_unauthorized_handshake_before_subscribing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = _HandshakeWebSocket()
    redis_factory = pytest.fail
    monkeypatch.setattr(ws_api, "_authenticate_runtime_thread", lambda _ws: _none())
    monkeypatch.setattr(ws_api.aioredis, "from_url", redis_factory)

    await ws_api.conversation_runtime_events(websocket)  # type: ignore[arg-type]

    assert websocket.closed_code == 4401


async def _none() -> None:
    return None


@pytest.mark.asyncio
async def test_runtime_handshake_timeout_is_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def timeout(awaitable, *_args, **_kwargs):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(ws_api.asyncio, "wait_for", timeout)

    assert await ws_api._authenticate_runtime_thread(_HandshakeWebSocket()) is None


@pytest.mark.asyncio
async def test_runtime_handshake_releases_the_authorization_session_before_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Session:
        async def get(self, _model, user_id: int):
            assert user_id == 7
            return SimpleNamespace(is_active=True)

    class _SessionContext:
        entered = False
        exited = False

        async def __aenter__(self):
            self.entered = True
            return _Session()

        async def __aexit__(self, *_args):
            self.exited = True

    context = _SessionContext()

    async def authorized_thread(session, user, thread_id: int):
        assert context.entered is True
        assert user.is_active is True
        assert thread_id == 81
        return SimpleNamespace(id=81)

    monkeypatch.setattr(ws_api, "async_session", lambda: context)
    monkeypatch.setattr(ws_api, "decode_token", lambda _token: {"sub": "7"})
    monkeypatch.setattr(ws_api, "get_conversation_thread", authorized_thread)

    assert await ws_api._authenticate_runtime_thread(_HandshakeWebSocket()) == 81
    assert context.exited is True


@pytest.mark.asyncio
async def test_runtime_channel_filters_other_threads_and_cleans_redis_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pubsub = _FakePubSub([
        '{"type":"brain.runtime.message_delta","payload":{"thread_id":82}}',
        '{"type":"brain.runtime.message_delta","payload":{"thread_id":81,"delta":"safe"}}',
    ])
    redis = _FakeRedis(pubsub)
    websocket = _RecordingWebSocket()

    async def authenticated(_ws) -> int:
        return 81

    monkeypatch.setattr(ws_api, "_authenticate_runtime_thread", authenticated)
    monkeypatch.setattr(ws_api.aioredis, "from_url", lambda *_args, **_kwargs: redis)

    await ws_api.conversation_runtime_events(websocket)  # type: ignore[arg-type]

    assert websocket.sent == [
        '{"type":"authenticated","thread_id":81}',
        '{"type":"brain.runtime.message_delta","payload":{"thread_id":81,"delta":"safe"}}'
    ]
    assert pubsub.unsubscribed is True
    assert pubsub.closed is True
    assert redis.closed is True


@pytest.mark.asyncio
async def test_runtime_channel_closes_and_cleans_resources_when_subscribe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SubscribeFailurePubSub(_FakePubSub):
        async def subscribe(self, _channel: str) -> None:
            raise RuntimeError("subscribe failed")

        async def unsubscribe(self, _channel: str) -> None:
            self.unsubscribed = True
            raise RuntimeError("unsubscribe cleanup failed")

        async def aclose(self) -> None:
            self.closed = True
            raise RuntimeError("pubsub cleanup failed")

    class _CleanupFailureRedis(_FakeRedis):
        async def aclose(self) -> None:
            self.closed = True
            raise RuntimeError("redis cleanup failed")

    pubsub = _SubscribeFailurePubSub()
    redis = _CleanupFailureRedis(pubsub)
    websocket = _HandshakeWebSocket()

    async def authenticated(_ws) -> int:
        return 81

    monkeypatch.setattr(ws_api, "_authenticate_runtime_thread", authenticated)
    monkeypatch.setattr(ws_api.aioredis, "from_url", lambda *_args, **_kwargs: redis)

    await ws_api.conversation_runtime_events(websocket)  # type: ignore[arg-type]

    assert websocket.closed_code == 1011
    assert pubsub.unsubscribed is True
    assert pubsub.closed is True
    assert redis.closed is True
