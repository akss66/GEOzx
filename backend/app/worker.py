"""arq Worker：消费事件队列。

process_event：落 Event 表 → 跑订阅处理器 → Redis pub/sub 广播给 WebSocket。
失败由 arq 重试（max_tries）。
"""

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.config import settings
from app.core.events import EVENTS_CHANNEL, dispatch, redis_settings
from app.db import async_session
from app.models import Event

log = logging.getLogger("dyflow.worker")


async def process_event(ctx: dict, event: dict[str, Any]) -> int:
    # 1) 落库（事件溯源）
    async with async_session() as session:
        row = Event(
            type=event["type"],
            payload=event.get("payload"),
            content_item_id=event.get("content_item_id"),
            project_id=event.get("project_id"),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        event_id = row.id

    # 2) 分发给进程内订阅者
    await dispatch(event["type"], event)

    # 3) 经 Redis 广播给 WebSocket 订阅者
    redis: aioredis.Redis = ctx["redis_pub"]
    await redis.publish(EVENTS_CHANNEL, json.dumps({**event, "id": event_id}))

    log.info("已处理事件 #%s type=%s", event_id, event["type"])
    return event_id


async def generate_video(ctx: dict, deliverable_id: int) -> int | None:
    """后台出片任务：真实调 Ark 生成→下载落本地卷→回写交付物→发事件。"""
    from app.core.events import publish_event
    from app.integrations.video_gen.tasks import generate_video_for_deliverable

    async with async_session() as session:
        asset = await generate_video_for_deliverable(
            session, deliverable_id, emit=publish_event
        )
        return asset.id if asset else None


async def on_startup(ctx: dict) -> None:
    ctx["redis_pub"] = aioredis.from_url(settings.redis_url, decode_responses=True)
    # 导入以注册处理器
    import app.core.event_handlers  # noqa: F401


async def on_shutdown(ctx: dict) -> None:
    await ctx["redis_pub"].aclose()


class WorkerSettings:
    functions = [process_event, generate_video]
    redis_settings = redis_settings()
    on_startup = on_startup
    on_shutdown = on_shutdown
    max_tries = 3
