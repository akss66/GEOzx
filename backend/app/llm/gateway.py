"""Model routing, streaming, and cost ledger for all LLM-backed agents."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.request_context import get_acting_user_id
from app.llm.adapters import CompletionResult, LLMAdapter
from app.llm.adapters.deepseek import DeepSeekAdapter
from app.llm.adapters.litellm import LiteLLMAdapter
from app.llm.cost import compute_cost
from app.models import LLMCall, ModelConfig
from app.services.model_infrastructure import (
    ROUTING_DEFAULTS,
    provider_code_for_model,
    provider_runtime,
)

StreamObserver = Callable[[dict[str, Any]], Awaitable[None]]
_stream_observer: ContextVar[StreamObserver | None] = ContextVar(
    "llm_stream_observer",
    default=None,
)


def set_stream_observer(observer: StreamObserver | None) -> Token[StreamObserver | None]:
    return _stream_observer.set(observer)


def reset_stream_observer(token: Token[StreamObserver | None]) -> None:
    _stream_observer.reset(token)


def provider_for(model: str) -> str:
    return provider_code_for_model(model)


class LLMError(RuntimeError):
    """Raised when every candidate model fails."""


class LLMGateway:
    def __init__(self, adapters: dict[str, LLMAdapter] | None = None) -> None:
        self._custom_adapters = adapters is not None
        self._adapters: dict[str, LLMAdapter] = adapters or {"litellm": LiteLLMAdapter()}

    async def _adapter(
        self, session: AsyncSession, org_id: int | None, model: str
    ) -> LLMAdapter:
        provider = provider_for(model)
        if self._custom_adapters:
            return self._adapters[provider]
        runtime = await provider_runtime(session, org_id, provider)
        if provider == "deepseek":
            return DeepSeekAdapter(
                api_key=runtime["api_key"],
                base_url=runtime["base_url"],
            )
        return self._adapters[provider]

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

    async def resolve_route(
        self, session: AsyncSession, org_id: int | None, agent_code: str
    ) -> tuple[str, str | None, dict[str, Any]]:
        if org_id is not None:
            cfg = await session.scalar(
                select(ModelConfig).where(
                    ModelConfig.org_id == org_id,
                    ModelConfig.agent_code == agent_code,
                )
            )
            if cfg is not None:
                params = dict(cfg.params or {})
                options = {
                    **ROUTING_DEFAULTS,
                    **dict(params.get("routing_config") or {}),
                }
                return cfg.primary_model, cfg.fallback_model, options
        return settings.llm_default_model, None, dict(ROUTING_DEFAULTS)

    async def chat(
        self,
        session: AsyncSession,
        org_id: int | None,
        agent_code: str,
        messages: list[dict],
    ) -> tuple[CompletionResult, float]:
        observer = _stream_observer.get()
        if observer is not None:
            return await self.chat_stream(session, org_id, agent_code, messages, observer)

        primary, fallback, options = await self.resolve_route(
            session, org_id, agent_code
        )
        candidates = [m for m in (primary, fallback) if m]

        last_exc: Exception | None = None
        for model in candidates:
            start = time.monotonic()
            try:
                adapter = await self._adapter(session, org_id, model)
                result = await adapter.complete(model, messages, options)
                latency = int((time.monotonic() - start) * 1000)
                cost = compute_cost(model, result.prompt_tokens, result.completion_tokens)
                await self._record(
                    session, org_id, agent_code, model, result, cost, latency, "ok", None
                )
                return result, cost
            except Exception as exc:  # noqa: BLE001 - record and try fallback
                latency = int((time.monotonic() - start) * 1000)
                await self._record(
                    session, org_id, agent_code, model, None, 0.0, latency, "error", str(exc)
                )
                last_exc = exc

        raise LLMError(f"all candidate models failed: {candidates}") from last_exc

    async def chat_stream(
        self,
        session: AsyncSession,
        org_id: int | None,
        agent_code: str,
        messages: list[dict],
        observer: StreamObserver,
    ) -> tuple[CompletionResult, float]:
        primary, fallback, options = await self.resolve_route(
            session, org_id, agent_code
        )
        candidates = [m for m in (primary, fallback) if m]

        last_exc: Exception | None = None
        for model in candidates:
            start = time.monotonic()
            chunks: list[str] = []
            try:
                adapter = await self._adapter(session, org_id, model)
                await observer(
                    {
                        "phase": "start",
                        "agent_code": agent_code,
                        "model": model,
                    }
                )
                async for chunk in adapter.stream(model, messages, options):
                    chunks.append(chunk)
                    await observer(
                        {
                            "phase": "delta",
                            "agent_code": agent_code,
                            "model": model,
                            "delta": chunk,
                        }
                    )
                content = "".join(chunks)
                result = CompletionResult(
                    content=content,
                    model=model,
                    prompt_tokens=0,
                    completion_tokens=_rough_token_count(content),
                    total_tokens=_rough_token_count(content),
                )
                latency = int((time.monotonic() - start) * 1000)
                cost = compute_cost(model, result.prompt_tokens, result.completion_tokens)
                await self._record(
                    session, org_id, agent_code, model, result, cost, latency, "ok", None
                )
                await observer(
                    {
                        "phase": "done",
                        "agent_code": agent_code,
                        "model": model,
                        "content": content,
                    }
                )
                return result, cost
            except Exception as exc:  # noqa: BLE001 - record and try fallback
                latency = int((time.monotonic() - start) * 1000)
                await self._record(
                    session, org_id, agent_code, model, None, 0.0, latency, "error", str(exc)
                )
                await observer(
                    {
                        "phase": "error",
                        "agent_code": agent_code,
                        "model": model,
                        "error": str(exc),
                    }
                )
                last_exc = exc

        raise LLMError(f"all candidate models failed: {candidates}") from last_exc

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
                created_by_id=get_acting_user_id(),
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


def _rough_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


gateway = LLMGateway()
