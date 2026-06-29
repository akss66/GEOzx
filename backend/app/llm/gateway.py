"""LLMGateway：按 per-Agent ModelConfig 路由首选/兜底模型，统一记账。

- `resolve_models`：从 ModelConfig 取 (primary, fallback)，无则用默认模型。
- `chat`：依次尝试 primary→fallback；每次调用（成功或失败）落 LLMCall 记录。
- 适配器可注入，便于测试；默认实例用真实 DeepSeek 适配器。
"""

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.llm.adapters import CompletionResult, LLMAdapter
from app.llm.adapters.deepseek import DeepSeekAdapter
from app.llm.cost import compute_cost
from app.models import LLMCall, ModelConfig


def provider_for(model: str) -> str:
    """按模型名推断供应商。v1 仅 DeepSeek；后续扩展更多前缀映射。"""
    return "deepseek"


class LLMError(RuntimeError):
    """所有候选模型均失败时抛出。"""


class LLMGateway:
    def __init__(self, adapters: dict[str, LLMAdapter] | None = None) -> None:
        self._adapters: dict[str, LLMAdapter] = adapters or {"deepseek": DeepSeekAdapter()}

    def _adapter(self, model: str) -> LLMAdapter:
        return self._adapters[provider_for(model)]

    async def resolve_models(
        self, session: AsyncSession, org_id: int | None, agent_code: str
    ) -> tuple[str, str | None]:
        if org_id is not None:
            cfg = await session.scalar(
                select(ModelConfig).where(
                    ModelConfig.org_id == org_id,
                    ModelConfig.agent_code == agent_code,
                )
            )
            if cfg is not None:
                return cfg.primary_model, cfg.fallback_model
        return settings.llm_default_model, None

    async def chat(
        self,
        session: AsyncSession,
        org_id: int | None,
        agent_code: str,
        messages: list[dict],
    ) -> tuple[CompletionResult, float]:
        primary, fallback = await self.resolve_models(session, org_id, agent_code)
        candidates = [m for m in (primary, fallback) if m]

        last_exc: Exception | None = None
        for model in candidates:
            start = time.monotonic()
            try:
                result = await self._adapter(model).complete(model, messages)
                latency = int((time.monotonic() - start) * 1000)
                cost = compute_cost(model, result.prompt_tokens, result.completion_tokens)
                await self._record(
                    session, org_id, agent_code, model, result, cost, latency, "ok", None
                )
                return result, cost
            except Exception as exc:  # noqa: BLE001 — 记录后继续尝试兜底
                latency = int((time.monotonic() - start) * 1000)
                await self._record(
                    session, org_id, agent_code, model, None, 0.0, latency, "error", str(exc)
                )
                last_exc = exc

        raise LLMError(f"所有候选模型均失败：{candidates}") from last_exc

    async def _record(
        self,
        session: AsyncSession,
        org_id: int | None,
        agent_code: str,
        model: str,
        result: CompletionResult | None,
        cost: float,
        latency_ms: int,
        status: str,
        error: str | None,
    ) -> None:
        session.add(
            LLMCall(
                org_id=org_id,
                agent_code=agent_code,
                provider=provider_for(model),
                model=model,
                prompt_tokens=result.prompt_tokens if result else 0,
                completion_tokens=result.completion_tokens if result else 0,
                total_tokens=result.total_tokens if result else 0,
                cost_usd=cost,
                latency_ms=latency_ms,
                status=status,
                error=error,
            )
        )
        await session.commit()


# 默认网关实例（真实 DeepSeek 适配器）
gateway = LLMGateway()
