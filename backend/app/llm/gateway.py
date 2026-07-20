"""Model routing, streaming, and cost ledger for all LLM-backed agents."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_context import get_acting_user_id
from app.llm.adapters import CompletionResult, LLMAdapter
from app.llm.adapters.deepseek import DeepSeekAdapter
from app.llm.adapters.litellm import LiteLLMAdapter
from app.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from app.llm.cost import compute_cost
from app.models import LLMCall
from app.services.model_infrastructure import (
    ModelTarget,
    provider_runtime,
    provider_runtime_for_target,
    redact_error,
    resolve_route_targets,
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
    return "litellm" if model.startswith("litellm:") else "deepseek"


class LLMError(RuntimeError):
    """Raised when every candidate model fails."""


class LLMGateway:
    def __init__(self, adapters: dict[str, LLMAdapter] | None = None) -> None:
        self._custom_adapters = adapters is not None
        self._adapters: dict[str, LLMAdapter] = adapters or {"litellm": LiteLLMAdapter()}

    async def _adapter(
        self,
        session: AsyncSession,
        org_id: int | None,
        target: ModelTarget,
    ) -> LLMAdapter:
        if target.provider_id is not None:
            runtime = await provider_runtime_for_target(
                session,
                org_id,
                target.provider_id,
                target.model,
            )
            if self._custom_adapters:
                return self._adapters[target.provider_code]
            return OpenAICompatibleAdapter(
                provider_code=target.provider_code,
                api_key=runtime["api_key"],
                base_url=str(runtime["base_url"]),
            )
        if self._custom_adapters:
            return self._adapters[target.provider_code]
        runtime = await provider_runtime(session, org_id, target.provider_code)
        if target.provider_code == "deepseek":
            return DeepSeekAdapter(
                api_key=runtime["api_key"],
                base_url=runtime["base_url"],
            )
        return self._adapters[target.provider_code]

    async def resolve_models(
        self, session: AsyncSession, org_id: int | None, agent_code: str
    ) -> tuple[str, str | None]:
        primary, fallback, _options = await resolve_route_targets(session, org_id, agent_code)
        return primary.model, fallback.model if fallback is not None else None

    async def resolve_route(
        self, session: AsyncSession, org_id: int | None, agent_code: str
    ) -> tuple[str, str | None, dict[str, Any]]:
        primary, fallback, options = await resolve_route_targets(session, org_id, agent_code)
        return primary.model, fallback.model if fallback is not None else None, options

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

        primary, fallback, options = await resolve_route_targets(session, org_id, agent_code)
        candidates = [candidate for candidate in (primary, fallback) if candidate is not None]

        last_exc: Exception | None = None
        for target in candidates:
            start = time.monotonic()
            try:
                adapter = await self._adapter(session, org_id, target)
                result = await adapter.complete(target.model, messages, options)
                latency = int((time.monotonic() - start) * 1000)
                cost = compute_cost(target.model, result.prompt_tokens, result.completion_tokens)
                await self._record(
                    session,
                    org_id,
                    agent_code,
                    target,
                    result,
                    cost,
                    latency,
                    "ok",
                    None,
                )
                return result, cost
            except Exception as exc:  # noqa: BLE001 - record and try fallback
                latency = int((time.monotonic() - start) * 1000)
                safe_error = _safe_error(exc)
                await self._record(
                    session,
                    org_id,
                    agent_code,
                    target,
                    None,
                    0.0,
                    latency,
                    "error",
                    safe_error,
                )
                last_exc = RuntimeError(safe_error)

        raise LLMError(
            f"all candidate models failed: {[item.model for item in candidates]}"
        ) from last_exc

    async def chat_stream(
        self,
        session: AsyncSession,
        org_id: int | None,
        agent_code: str,
        messages: list[dict],
        observer: StreamObserver,
    ) -> tuple[CompletionResult, float]:
        primary, fallback, options = await resolve_route_targets(session, org_id, agent_code)
        candidates = [candidate for candidate in (primary, fallback) if candidate is not None]

        last_exc: Exception | None = None
        for target in candidates:
            start = time.monotonic()
            chunks: list[str] = []
            try:
                adapter = await self._adapter(session, org_id, target)
                await observer(
                    {
                        "phase": "start",
                        "agent_code": agent_code,
                        "model": target.model,
                    }
                )
                async for chunk in adapter.stream(target.model, messages, options):
                    chunks.append(chunk)
                    await observer(
                        {
                            "phase": "delta",
                            "agent_code": agent_code,
                            "model": target.model,
                            "delta": chunk,
                        }
                    )
                content = "".join(chunks)
                result = CompletionResult(
                    content=content,
                    model=target.model,
                    prompt_tokens=0,
                    completion_tokens=_rough_token_count(content),
                    total_tokens=_rough_token_count(content),
                )
                latency = int((time.monotonic() - start) * 1000)
                cost = compute_cost(target.model, result.prompt_tokens, result.completion_tokens)
                await self._record(
                    session,
                    org_id,
                    agent_code,
                    target,
                    result,
                    cost,
                    latency,
                    "ok",
                    None,
                )
                await observer(
                    {
                        "phase": "done",
                        "agent_code": agent_code,
                        "model": target.model,
                        "content": content,
                    }
                )
                return result, cost
            except Exception as exc:  # noqa: BLE001 - record and try fallback
                latency = int((time.monotonic() - start) * 1000)
                safe_error = _safe_error(exc)
                await self._record(
                    session,
                    org_id,
                    agent_code,
                    target,
                    None,
                    0.0,
                    latency,
                    "error",
                    safe_error,
                )
                await observer(
                    {
                        "phase": "error",
                        "agent_code": agent_code,
                        "model": target.model,
                        "error": safe_error,
                    }
                )
                last_exc = RuntimeError(safe_error)

        raise LLMError(
            f"all candidate models failed: {[item.model for item in candidates]}"
        ) from last_exc

    async def _record(
        self,
        session: AsyncSession,
        org_id: int | None,
        agent_code: str,
        target: ModelTarget,
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
                provider=target.provider_code,
                model=target.model,
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


def _safe_error(exc: Exception) -> str:
    return redact_error(str(exc)) or "model provider request failed"


gateway = LLMGateway()
