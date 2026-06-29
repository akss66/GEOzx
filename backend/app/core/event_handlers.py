"""内置事件处理器（worker 启动时导入以完成注册）。

这里放跨切面的订阅者；编排引擎的处理器在 T7 接入。
"""

import logging

from app.core.events import subscribe

log = logging.getLogger("dyflow.events")


@subscribe("demo.ping")
async def on_demo_ping(event: dict) -> None:
    """T6 演示处理器：仅记录日志，证明订阅/分发链路可用。"""
    log.info("demo.ping handler 收到事件: %s", event.get("payload"))
