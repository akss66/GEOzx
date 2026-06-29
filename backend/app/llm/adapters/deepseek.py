"""DeepSeek 适配器（OpenAI 兼容 /chat/completions）。"""

import httpx

from app.config import settings
from app.llm.adapters import CompletionResult


class DeepSeekAdapter:
    provider = "deepseek"

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self._key = api_key if api_key is not None else settings.deepseek_api_key
        self._base = (base_url or settings.deepseek_base_url).rstrip("/")

    async def complete(self, model: str, messages: list[dict]) -> CompletionResult:
        if not self._key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json={"model": model, "messages": messages},
            )
            resp.raise_for_status()
            data = resp.json()

        usage = data.get("usage", {})
        return CompletionResult(
            content=data["choices"][0]["message"]["content"],
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )
