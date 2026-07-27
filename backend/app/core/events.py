"""事件总线：发布（arq 入队）+ 进程内订阅/分发 + Redis 广播频道。

事件流：publish_event() 入 arq 队列 → worker.process_event 消费 →
落 Event 表 + 跑订阅处理器 + 经 Redis pub/sub 广播给 WebSocket 订阅者。
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from app.config import settings
from app.db import get_redis

# WebSocket 广播用的 Redis pub/sub 频道
EVENTS_CHANNEL = "dyflow:events"

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]
_handlers: dict[str, list[EventHandler]] = {}


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


# —— 进程内订阅/分发（worker 内运行）——


def subscribe(event_type: str) -> Callable[[EventHandler], EventHandler]:
    """注册某事件类型的处理器（装饰器）。"""

    def _decorator(fn: EventHandler) -> EventHandler:
        _handlers.setdefault(event_type, []).append(fn)
        return fn

    return _decorator


async def dispatch(event_type: str, event: dict[str, Any]) -> None:
    """把事件分发给所有已注册的处理器。"""
    for handler in _handlers.get(event_type, []):
        await handler(event)


def handlers_for(event_type: str) -> list[EventHandler]:
    return list(_handlers.get(event_type, []))


# —— 发布（API 侧入队）——

_pool = None


async def get_arq_pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def publish_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    content_item_id: int | None = None,
    project_id: int | None = None,
) -> None:
    """发布事件：入 arq 队列，由 worker 异步消费。"""
    pool = await get_arq_pool()
    await pool.enqueue_job(
        "process_event",
        {
            "type": event_type,
            "payload": payload,
            "content_item_id": content_item_id,
            "project_id": project_id,
        },
    )


async def publish_realtime_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    content_item_id: int | None = None,
    project_id: int | None = None,
    *,
    event_id: int | None = None,
) -> None:
    """Broadcast an ephemeral event directly to WebSocket subscribers.

    Token deltas can be very frequent, so they should not be queued through arq
    or persisted as Event rows. Durable checkpoints still use publish_event or
    explicit Event inserts.
    """
    event = {
        "id": event_id,
        "type": event_type,
        "payload": payload,
        "content_item_id": content_item_id,
        "project_id": project_id,
    }
    await dispatch(event_type, event)
    redis = get_redis()
    try:
        async with asyncio.timeout(0.5):
            await redis.publish(EVENTS_CHANNEL, json.dumps(event, ensure_ascii=False))
    except (TimeoutError, OSError):
        # Realtime token streaming is best-effort; durable checkpoints are
        # recorded separately and must not fail because Redis is unavailable.
        return
    except Exception:
        # Redis client failures vary by transport and platform. This boundary
        # must never hold up the user-facing response path.
        return
