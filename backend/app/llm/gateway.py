"""Model routing, streaming, and cost ledger for all LLM-backed agents."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.request_context import get_acting_user_id
from app.core.runtime_failures import (
    ProviderRuntimeFailure,
    exception_chain,
    http_status_code,
)
from app.llm.adapters import CompletionResult
from app.llm.adapters.deepseek import DeepSeekAdapter
from app.llm.adapters.deterministic_test import DeterministicTestAdapter
from app.llm.adapters.litellm import LiteLLMAdapter
from app.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from app.llm.cost import compute_cost
from app.models import LLMCall
from app.services.model_infrastructure import (
    ModelRouteConfigurationError,
    ModelTarget,
    provider_runtime,
    provider_runtime_for_target,
    redact_error,
    resolve_route_targets,
)
from app.services.turn_observability import (
    increment_model_call_count,
    record_first_user_token,
)

StreamObserver = Callable[[dict[str, Any]], Awaitable[None]]
_stream_observer: ContextVar[StreamObserver | None] = ContextVar(
    "llm_stream_observer",
    default=None,
)


class _GatewayAdapter(Protocol):
    provider: str

    async def complete(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> CompletionResult: ...

    def stream(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]: ...


@dataclass(frozen=True)
class LLMCallContext:
    """Safe per-call metadata and provider request hints."""

    task_id: int | None = None
    invocation_id: int | None = None
    trace_id: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    prompt_schema_version: str | None = None
    scope: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    response_format: dict[str, str] | None = None


_call_context: ContextVar[LLMCallContext | None] = ContextVar(
    "llm_call_context",
    default=None,
)


@contextmanager
def bind_llm_call_context(context: LLMCallContext):
    token = _call_context.set(context)
    try:
        yield context
    finally:
        _call_context.reset(token)


def current_llm_call_context() -> LLMCallContext | None:
    return _call_context.get()


def set_stream_observer(observer: StreamObserver | None) -> Token[StreamObserver | None]:
    return _stream_observer.set(observer)


def reset_stream_observer(token: Token[StreamObserver | None]) -> None:
    _stream_observer.reset(token)


def provider_for(model: str) -> str:
    return "litellm" if model.startswith("litellm:") else "deepseek"


class LLMError(RuntimeError):
    """Raised when every candidate model fails."""


class LLMGateway:
    def __init__(self, adapters: dict[str, _GatewayAdapter] | None = None) -> None:
        self._custom_adapters = adapters is not None
        self._adapters: dict[str, _GatewayAdapter] = (
            adapters if adapters is not None else {"litellm": LiteLLMAdapter()}
        )

    async def _adapter(
        self,
        session: AsyncSession,
        org_id: int | None,
        target: ModelTarget,
    ) -> _GatewayAdapter:
        if (
            not self._custom_adapters
            and settings.environment == "test"
            and settings.llm_deterministic_test_provider_enabled
        ):
            return DeterministicTestAdapter()
        if target.provider_id is not None:
            provider_runtime_data = await provider_runtime_for_target(
                session,
                org_id,
                target.provider_id,
                target.model,
            )
            if self._custom_adapters:
                return self._adapters[target.provider_code]
            adapter_options: dict[str, Any] = {
                "provider_code": target.provider_code,
                "api_key": provider_runtime_data["api_key"],
                "base_url": str(provider_runtime_data["base_url"]),
            }
            if provider_runtime_data["allow_mixed_dns"]:
                adapter_options["allow_mixed_dns"] = True
            return OpenAICompatibleAdapter(
                **adapter_options,
            )
        if self._custom_adapters:
            return self._adapters[target.provider_code]
        legacy_runtime = await provider_runtime(session, org_id, target.provider_code)
        if target.provider_code == "deepseek":
            return DeepSeekAdapter(
                api_key=legacy_runtime["api_key"],
                base_url=legacy_runtime["base_url"],
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
        options = _effective_options(options)
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
        options = _effective_options(options)
        candidates = [candidate for candidate in (primary, fallback) if candidate is not None]
        candidates = _limit_candidates(candidates)

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
                last_exc = (
                    exc
                    if isinstance(exc, ModelRouteConfigurationError)
                    else _provider_runtime_failure(exc, safe_error=safe_error)
                )

        raise LLMError("all candidate models failed") from last_exc

    async def chat_stream(
        self,
        session: AsyncSession,
        org_id: int | None,
        agent_code: str,
        messages: list[dict],
        observer: StreamObserver,
    ) -> tuple[CompletionResult, float]:
        primary, fallback, options = await resolve_route_targets(session, org_id, agent_code)
        options = _effective_options(options)
        candidates = [candidate for candidate in (primary, fallback) if candidate is not None]
        candidates = _limit_candidates(candidates)

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
                    await record_first_user_token(
                        session,
                        agent_code=agent_code,
                        delta=chunk,
                    )
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
                last_exc = (
                    exc
                    if isinstance(exc, ModelRouteConfigurationError)
                    else _provider_runtime_failure(exc, safe_error=safe_error)
                )

        raise LLMError("all candidate models failed") from last_exc

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
        call_context = _call_context.get()
        session.add(
            LLMCall(
                org_id=org_id,
                created_by_id=get_acting_user_id(),
                task_id=call_context.task_id if call_context else None,
                invocation_id=call_context.invocation_id if call_context else None,
                trace_id=call_context.trace_id if call_context else None,
                agent_code=agent_code,
                prompt_id=call_context.prompt_id if call_context else None,
                prompt_version=call_context.prompt_version if call_context else None,
                prompt_hash=call_context.prompt_hash if call_context else None,
                prompt_schema_version=(
                    call_context.prompt_schema_version if call_context else None
                ),
                scope=dict(call_context.scope) if call_context else {},
                budget=dict(call_context.budget) if call_context else {},
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
        await increment_model_call_count(session)
        await session.commit()


def _effective_options(options: dict[str, Any]) -> dict[str, Any]:
    effective = dict(options)
    call_context = _call_context.get()
    if call_context is not None and call_context.response_format is not None:
        effective["response_format"] = dict(call_context.response_format)
    if call_context is not None and call_context.budget.get("timeout_seconds") is not None:
        effective["timeout_seconds"] = min(
            float(effective.get("timeout_seconds") or 90),
            float(call_context.budget["timeout_seconds"]),
        )
    if (
        call_context is not None
        and call_context.prompt_id
        and settings.environment == "test"
        and settings.llm_deterministic_test_provider_enabled
    ):
        effective["_deterministic_prompt_id"] = call_context.prompt_id
    return effective


def _limit_candidates(candidates: list[ModelTarget]) -> list[ModelTarget]:
    call_context = _call_context.get()
    if call_context is None or call_context.budget.get("max_attempts") is None:
        return candidates
    limit = max(1, int(call_context.budget["max_attempts"]))
    return candidates[:limit]


def _rough_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


def _safe_error(exc: Exception) -> str:
    return redact_error(str(exc)) or "model provider request failed"


def _provider_runtime_failure(
    exc: Exception,
    *,
    safe_error: str,
) -> ProviderRuntimeFailure:
    chain = exception_chain(exc)
    status_code = next(
        (code for item in chain if (code := http_status_code(item)) is not None),
        None,
    )
    if status_code is not None:
        failure_kind = "http"
    elif any(isinstance(item, (TimeoutError, httpx.TimeoutException)) for item in chain):
        failure_kind = "timeout"
    elif any(isinstance(item, (ConnectionError, httpx.NetworkError)) for item in chain):
        failure_kind = "connection"
    else:
        failure_kind = "unknown"
    return ProviderRuntimeFailure(
        status_code=status_code,
        failure_kind=failure_kind,
        safe_message=(
            safe_error if "[REDACTED]" in safe_error else "model provider request failed"
        ),
    )


gateway = LLMGateway()
