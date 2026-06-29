"""火山方舟豆包 Seedance 视频生成适配器（Ark v3 异步任务）。

API 形态（Ark contents/generations/tasks）：
- 提交：POST {base}/contents/generations/tasks
    body: {"model": <id>, "content": [{"type": "text", "text": "<prompt> --ratio 9:16 --dur 5"}]}
    resp: {"id": "<task_id>", ...}
- 查询：GET {base}/contents/generations/tasks/{task_id}
    resp: {"id", "status": "queued|running|succeeded|failed",
           "content": {"video_url": "..."}, "error": {...}}
认证：Authorization: Bearer <ARK_API_KEY>。
"""

import httpx

from app.config import settings
from app.integrations.video_gen import VideoGenResult


class ArkVideoAdapter:
    provider = "ark"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._key = api_key if api_key is not None else settings.ark_api_key
        self._base = (base_url or settings.ark_base_url).rstrip("/")
        self._model = model or settings.ark_video_model

    def _headers(self) -> dict[str, str]:
        if not self._key:
            raise RuntimeError("未配置 ARK_API_KEY")
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    async def submit(self, prompt: str, *, ratio: str = "9:16", duration: int = 5) -> str:
        # Ark 用文本指令尾参传递比例/时长（--ratio / --dur）
        text = f"{prompt} --ratio {ratio} --dur {duration}"
        body = {"model": self._model, "content": [{"type": "text", "text": text}]}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base}/contents/generations/tasks",
                headers=self._headers(),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        task_id = data.get("id") or data.get("task_id")
        if not task_id:
            raise RuntimeError(f"提交未返回 task_id：{data}")
        return task_id

    async def poll(self, task_id: str) -> VideoGenResult:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{self._base}/contents/generations/tasks/{task_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        status = data.get("status", "unknown")
        content = data.get("content") or {}
        video_url = content.get("video_url") if isinstance(content, dict) else None
        error = data.get("error")
        err_msg = (
            error.get("message")
            if isinstance(error, dict)
            else (str(error) if error else None)
        )
        return VideoGenResult(
            task_id=task_id, status=status, video_url=video_url, error=err_msg
        )
