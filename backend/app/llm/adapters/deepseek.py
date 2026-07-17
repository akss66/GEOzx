"""DeepSeek adapter using the OpenAI-compatible chat completion API."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import settings
from app.llm.adapters import CompletionResult


class DeepSeekAdapter:
    provider = "deepseek"

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self._key = api_key if api_key is not None else settings.deepseek_api_key
        self._base = (base_url or settings.deepseek_base_url).rstrip("/")

    async def complete(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> CompletionResult:
        if not self._key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        request_options = _request_options(options)
        timeout = float(request_options.pop("timeout", 60.0))
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json={"model": model, "messages": messages, **request_options},
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

    async def stream(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        if not self._key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        request_options = _request_options(options)
        timeout = float(request_options.pop("timeout", 60.0))
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base}/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    **request_options,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = payload.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield str(content)


def _request_options(options: dict[str, Any] | None) -> dict[str, Any]:
    if not options:
        return {}
    return {
        "temperature": options["temperature"],
        "max_tokens": options["max_tokens"],
        "timeout": options["timeout_seconds"],
    }
