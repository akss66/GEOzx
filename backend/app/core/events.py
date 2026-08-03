"""事件总线：发布（arq 入队）+ 进程内订阅/分发 + Redis 广播频道。

事件流：publish_event() 入 arq 队列 → worker.process_event 消费 →
落 Event 表 + 跑订阅处理器 + 经 Redis pub/sub 广播给 WebSocket 订阅者。
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeAlias

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_redis
from app.models import Event

# WebSocket 广播用的 Redis pub/sub 频道
EVENTS_CHANNEL = "dyflow:events"

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]
_handlers: dict[str, list[EventHandler]] = {}

TurnEventPayload: TypeAlias = dict[str, object]


class TurnEventScopeLike(Protocol):
    org_id: int
    account_id: int
    thread_id: int
    turn_id: int
    run_id: int | None
    skill_run_id: int | None


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


def runtime_event_idempotency_key(
    *,
    org_id: int,
    account_id: int,
    run_id: int,
    client_message_id: str,
    event_type: str,
    semantic_key: str,
) -> str:
    """Return the stable database key for one user-visible runtime write."""

    normalized = json.dumps(
        [
            int(org_id),
            int(account_id),
            int(run_id),
            str(client_message_id),
            str(event_type),
            str(semantic_key),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def turn_event_idempotency_key(
    scope: TurnEventScopeLike,
    idempotency_key: str,
) -> str:
    """Namespace a logical Turn event inside the existing 64-character key."""

    normalized = json.dumps(
        [
            "turn-event-v1",
            int(scope.org_id),
            int(scope.account_id),
            int(scope.thread_id),
            int(scope.turn_id),
            int(scope.run_id) if scope.run_id is not None else None,
            int(scope.skill_run_id) if scope.skill_run_id is not None else None,
            str(idempotency_key),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def record_runtime_event_once(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    run_id: int,
    client_message_id: str,
    event_type: str,
    semantic_key: str,
    payload: dict[str, Any],
    content_item_id: int | None = None,
    project_id: int | None = None,
) -> tuple[Event, bool]:
    """Persist a runtime event once, even when independent workers replay it.

    The initial lookup makes repeated calls in one transaction cheap. The unique
    constraint is the actual cross-worker arbiter; its conflict is contained in
    a savepoint so the caller's other pending work remains intact.
    """

    if not client_message_id:
        raise ValueError("runtime event idempotency requires client_message_id")
    if not semantic_key:
        raise ValueError("runtime event idempotency requires semantic_key")
    key = runtime_event_idempotency_key(
        org_id=org_id,
        account_id=account_id,
        run_id=run_id,
        client_message_id=client_message_id,
        event_type=event_type,
        semantic_key=semantic_key,
    )
    existing = await session.scalar(
        select(Event).where(Event.idempotency_key == key)
    )
    if existing is not None:
        return existing, False

    event_payload = {
        **payload,
        "org_id": org_id,
        "account_id": account_id,
        "run_id": run_id,
        "client_message_id": client_message_id,
        "semantic_key": semantic_key,
    }
    try:
        async with session.begin_nested():
            row = Event(
                type=event_type,
                content_item_id=content_item_id,
                project_id=project_id,
                payload=event_payload,
                idempotency_key=key,
            )
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(Event).where(Event.idempotency_key == key)
        )
        if existing is None:
            raise
        return existing, False
    return row, True


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
    from app.services.runtime_phase import with_runtime_phase

    public_payload = with_runtime_phase(event_type, payload)
    event = {
        "id": event_id,
        "type": event_type,
        "payload": public_payload,
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
