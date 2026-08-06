"""DeepSeek adapter preserved as a compatibility wrapper."""

from collections.abc import AsyncIterator
from typing import Any

from app.config import settings
from app.llm.adapters import CompletionResult
from app.llm.adapters.openai_compatible import OpenAICompatibleAdapter


class DeepSeekAdapter(OpenAICompatibleAdapter):
    provider = "deepseek"

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self._resolved_key = api_key if api_key is not None else settings.deepseek_api_key
        resolved_base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        super().__init__(
            provider_code="deepseek",
            api_key=self._resolved_key,
            base_url=resolved_base_url,
            allow_mixed_dns=resolved_base_url == "https://api.deepseek.com",
        )

    async def complete(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> CompletionResult:
        self._require_key()
        return await super().complete(model, messages, options)

    async def stream(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        self._require_key()
        async for chunk in super().stream(model, messages, options):
            yield chunk

    def _require_key(self) -> None:
        if not self._resolved_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
