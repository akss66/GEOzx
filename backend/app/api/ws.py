"""WebSocket：/ws/events 订阅 Redis 广播频道，实时转发事件给前端。"""

import asyncio
import json
from collections.abc import Mapping

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.core.events import EVENTS_CHANNEL
from app.core.security import decode_token
from app.db import async_session
from app.models import User
from app.services.conversations import get_conversation_thread

router = APIRouter()

_PUBLIC_TURN_EVENT_TYPES = frozenset(
    {
        "turn.received",
        "turn.completed",
        "turn.failed",
        "turn.blocked",
        "turn.cancelled",
        "turn.stopped",
        "step.started",
        "step.completed",
        "step.failed",
        "deliverable.updated",
    }
)

_THREAD_RUNTIME_EVENT_TYPES = frozenset(
    {
        "brain.runtime.message_start",
        "brain.runtime.message_delta",
        "brain.runtime.message_done",
        "brain.runtime.message_error",
    }
)


def _should_forward_legacy_event(data: str) -> bool:
    """Keep unscoped legacy events available, never broadcast Turn-scoped data."""

    try:
        parsed = json.loads(data)
    except (TypeError, json.JSONDecodeError):
        return not _looks_like_private_turn_event(data)
    if not isinstance(parsed, Mapping):
        return True
    event_type = parsed.get("type")
    if isinstance(event_type, str) and (
        event_type in _PUBLIC_TURN_EVENT_TYPES
        or event_type.startswith("turn.")
        or event_type.startswith("step.")
        or event_type == "deliverable.updated"
    ):
        return False
    if "thread_id" in parsed or "turn_id" in parsed:
        return False
    payload = parsed.get("payload")
    return not isinstance(payload, Mapping) or (
        "thread_id" not in payload and "turn_id" not in payload
    )


def _looks_like_private_turn_event(data: object) -> bool:
    if not isinstance(data, str):
        return False
    normalized = data.lower()
    return (
        '"thread_id"' in normalized
        or '"turn_id"' in normalized
        or '"type":"turn.' in normalized
        or '"type": "turn.' in normalized
        or '"type":"step.' in normalized
        or '"type": "step.' in normalized
        or '"type":"deliverable.updated' in normalized
        or '"type": "deliverable.updated' in normalized
    )


def _runtime_event_for_thread(data: str, thread_id: int) -> bool:
    """Accept only ephemeral text frames that explicitly belong to this Thread."""

    try:
        parsed = json.loads(data)
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(parsed, Mapping) or parsed.get("type") not in _THREAD_RUNTIME_EVENT_TYPES:
        return False
    payload = parsed.get("payload")
    return isinstance(payload, Mapping) and payload.get("thread_id") == thread_id


def _is_closed_transport_error(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    return (
        "handler is closed" in message
        or ("transport" in message and "closed=true" in message)
        or (
            "websocket.send" in message
            and ("websocket.close" in message or "response already completed" in message)
        )
        or 'cannot call "send" once a close message has been sent' in message
    )


async def _close_runtime_resources(pubsub, redis) -> None:
    """Release partially initialized runtime resources without masking the cause."""

    if pubsub is not None:
        try:
            await pubsub.unsubscribe(EVENTS_CHANNEL)
        except Exception:  # noqa: BLE001 - teardown cannot replace the stream failure
            pass
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001 - teardown cannot replace the stream failure
            pass
    if redis is not None:
        try:
            await redis.aclose()
        except Exception:  # noqa: BLE001 - teardown cannot replace the stream failure
            pass


async def _close_runtime_server_error(ws: WebSocket) -> None:
    try:
        await ws.close(code=1011)
    except Exception:  # noqa: BLE001 - the peer may already be gone
        pass


async def _authenticate_runtime_thread(ws: WebSocket) -> int | None:
    """Authorize a native WebSocket without exposing its bearer token in a URL."""

    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=5)
        if not isinstance(hello, Mapping) or hello.get("type") != "authenticate":
            return None
        token = hello.get("token")
        thread_id = hello.get("thread_id")
        if not isinstance(token, str) or not isinstance(thread_id, int) or thread_id <= 0:
            return None
        payload = decode_token(token)
        subject = payload.get("sub")
        if not subject:
            return None
        async with async_session() as session:
            user = await session.get(User, int(subject))
            if user is None or not user.is_active:
                return None
            await get_conversation_thread(session, user, thread_id)
        return thread_id
    except Exception:  # noqa: BLE001 - all handshake failures use one close policy
        # This boundary accepts only a valid authenticated Thread scope. The
        # caller receives the same policy close for malformed and unauthorized
        # handshakes so a Thread cannot be probed.
        return None


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.accept()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(EVENTS_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                if not _should_forward_legacy_event(message["data"]):
                    continue
                try:
                    await ws.send_text(message["data"])
                except RuntimeError as exc:
                    if not _is_closed_transport_error(exc):
                        raise
                    break
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(EVENTS_CHANNEL)
        await pubsub.aclose()
        await redis.aclose()


@router.websocket("/ws/conversation-runtime")
async def conversation_runtime_events(ws: WebSocket) -> None:
    """Short-lived authenticated channel for one Thread's transient text frames."""

    await ws.accept()
    thread_id = await _authenticate_runtime_thread(ws)
    if thread_id is None:
        await ws.close(code=4401)
        return

    redis = None
    pubsub = None
    try:
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe(EVENTS_CHANNEL)
        acknowledgement = json.dumps(
            {"type": "authenticated", "thread_id": thread_id},
            separators=(",", ":"),
        )
        await ws.send_text(acknowledgement)
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            if not _runtime_event_for_thread(message["data"], thread_id):
                continue
            try:
                await ws.send_text(message["data"])
            except RuntimeError as exc:
                if not _is_closed_transport_error(exc):
                    raise
                break
    except WebSocketDisconnect:
        pass
    except (asyncio.CancelledError, SystemExit):
        raise
    except Exception:  # noqa: BLE001 - runtime setup and stream failures share one safe close policy
        await _close_runtime_server_error(ws)
    finally:
        await _close_runtime_resources(pubsub, redis)
