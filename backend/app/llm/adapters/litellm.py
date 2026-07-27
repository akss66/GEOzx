"""Optional LiteLLM adapter.

Model configs opt into this adapter by prefixing model names with ``litellm:``.
The dependency is imported lazily so the default DeepSeek path remains unchanged.
"""

import importlib
from collections.abc import AsyncIterator
from typing import Any

from app.llm.adapters import CompletionResult

LITELLM_PREFIX = "litellm:"


class LiteLLMAdapter:
    provider = "litellm"

    async def complete(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> CompletionResult:
        actual_model = model.removeprefix(LITELLM_PREFIX)
        litellm = importlib.import_module("litellm")
        response = await litellm.acompletion(
            model=actual_model,
            messages=messages,
            **_request_options(options),
        )

        usage = _read(response, "usage", {}) or {}
        choices = _read(response, "choices", [])
        message = _read(choices[0], "message", {}) if choices else {}

        return CompletionResult(
            content=_read(message, "content", ""),
            model=model,
            prompt_tokens=_read(usage, "prompt_tokens", 0),
            completion_tokens=_read(usage, "completion_tokens", 0),
            total_tokens=_read(usage, "total_tokens", 0),
        )

    async def stream(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        actual_model = model.removeprefix(LITELLM_PREFIX)
        litellm = importlib.import_module("litellm")
        response = await litellm.acompletion(
            model=actual_model,
            messages=messages,
            stream=True,
            **_request_options(options),
        )
        async for chunk in response:
            choices = _read(chunk, "choices", [])
            if not choices:
                continue
            delta = _read(choices[0], "delta", {})
            content = _read(delta, "content", "")
            if content:
                yield str(content)


def _read(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _request_options(options: dict[str, Any] | None) -> dict[str, Any]:
    if not options:
        return {}
    result = {
        "temperature": options["temperature"],
        "max_tokens": options["max_tokens"],
        "timeout": options["timeout_seconds"],
    }
    if "response_format" in options:
        result["response_format"] = options["response_format"]
    return result
