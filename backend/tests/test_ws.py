from __future__ import annotations

import pytest

from app.api import ws as ws_api


class _FakePubSub:
    def __init__(self) -> None:
        self.unsubscribed = False
        self.closed = False

    async def subscribe(self, _channel: str) -> None:
        return None

    async def listen(self):
        yield {"type": "message", "data": "payload"}

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
