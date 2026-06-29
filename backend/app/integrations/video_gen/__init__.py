"""视频生成适配器接口与统一返回结构。

可切换后端：v1 火山方舟豆包 Seedance（ArkVideoAdapter）；后期独立 Seedance API 再加适配器。
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class VideoGenResult:
    """一次视频生成的结果。"""

    task_id: str
    status: str  # queued / running / succeeded / failed
    video_url: str | None = None
    error: str | None = None


@runtime_checkable
class VideoGenAdapter(Protocol):
    """视频生成供应商适配器接口（异步任务：提交→轮询→取 URL）。"""

    provider: str

    async def submit(self, prompt: str, *, ratio: str = "9:16", duration: int = 5) -> str:
        """提交生成任务，返回 task_id。失败抛异常。"""
        ...

    async def poll(self, task_id: str) -> VideoGenResult:
        """查询任务状态/结果。"""
        ...
