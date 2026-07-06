"""Optional LiteLLM adapter.

Model configs opt into this adapter by prefixing model names with ``litellm:``.
The dependency is imported lazily so the default DeepSeek path remains unchanged.
"""

import importlib
from typing import Any

from app.llm.adapters import CompletionResult

LITELLM_PREFIX = "litellm:"


class LiteLLMAdapter:
    provider = "litellm"

    async def complete(self, model: str, messages: list[dict]) -> CompletionResult:
        actual_model = model.removeprefix(LITELLM_PREFIX)
        litellm = importlib.import_module("litellm")
        response = await litellm.acompletion(model=actual_model, messages=messages)

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


def _read(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
