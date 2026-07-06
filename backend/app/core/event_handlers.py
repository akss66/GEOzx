"""内置事件处理器（worker 启动时导入以完成注册）。

这里放跨切面的订阅者；编排引擎的处理器在 T7 接入。
"""

import logging

from app.core.events import get_arq_pool, subscribe
from app.models.enums import ContentStage

log = logging.getLogger("dyflow.events")


@subscribe("agent.done")
async def on_agent_done(event: dict) -> None:
    """视频创作阶段完成 → 入队后台出片任务（异步，不阻塞编排）。"""
    payload = event.get("payload") or {}
    if payload.get("stage") != ContentStage.VIDEO_CREATION.value:
        return
    deliverable_id = payload.get("deliverable_id")
    if deliverable_id is None:
        return
    pool = await get_arq_pool()
    await pool.enqueue_job("generate_video", deliverable_id)
    log.info("已入队出片任务 deliverable=%s", deliverable_id)
