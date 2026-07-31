"""WebSocket：/ws/events 订阅 Redis 广播频道，实时转发事件给前端。"""

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.core.events import EVENTS_CHANNEL

router = APIRouter()


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


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.accept()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(EVENTS_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
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
