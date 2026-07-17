"""LLM adapter contracts and common return structures."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class CompletionResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@runtime_checkable
class LLMAdapter(Protocol):
    """Provider adapter contract used by LLMGateway."""

    provider: str

    async def complete(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> CompletionResult:
        """Run a non-streaming chat completion."""
        ...

    async def stream(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks from a streaming chat completion."""
        ...
