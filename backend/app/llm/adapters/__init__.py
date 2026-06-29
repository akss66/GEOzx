"""适配器接口与统一返回结构。"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class CompletionResult:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@runtime_checkable
class LLMAdapter(Protocol):
    """各模型供应商适配器需实现的接口。"""

    provider: str

    async def complete(self, model: str, messages: list[dict]) -> CompletionResult:
        """执行一次对话补全；失败抛异常（由网关捕获并兜底）。"""
        ...
