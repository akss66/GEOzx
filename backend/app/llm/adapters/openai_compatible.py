"""OpenAI-compatible adapter with bounded outbound request enforcement."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.core.outbound_url import (
    DEFAULT_OUTBOUND_REQUEST_POLICY,
    OutboundRequestPolicy,
    bounded_outbound_request,
    bounded_outbound_stream,
)
from app.llm.adapters import CompletionResult

_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class OpenAICompatibleAdapter:
    provider = "openai_compatible"

    def __init__(
        self,
        *,
        provider_code: str,
        api_key: str | None,
        base_url: str,
    ) -> None:
        self._provider_code = provider_code
        self._key = api_key
        self._base = base_url.rstrip("/")

    async def complete(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> CompletionResult:
        response = await bounded_outbound_request(
            "POST",
            f"{self._base}/chat/completions",
            headers=_headers(self._key),
            json={
                "model": model,
                "messages": messages,
                **_request_options(options),
            },
            policy=_request_policy(options),
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"{self._provider_code} request failed with status {response.status_code}"
            )
        try:
            data = response.json()
            message = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"{self._provider_code} returned an incompatible completion payload"
            ) from exc

        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        return CompletionResult(
            content=str(message),
            model=model,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
        )

    async def stream(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        async with bounded_outbound_stream(
            "POST",
            f"{self._base}/chat/completions",
            headers=_headers(self._key),
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                **_request_options(options),
            },
            policy=_request_policy(options),
        ) as response:
            if response.status_code >= 400:
                raise RuntimeError(
                    f"{self._provider_code} request failed with status {response.status_code}"
                )
            async for data in _iter_sse_data_lines(response):
                if data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = payload.get("choices") if isinstance(payload, dict) else None
                if not choices:
                    continue
                delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                if content:
                    yield str(content)


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _request_options(options: dict[str, Any] | None) -> dict[str, Any]:
    if not options:
        return {}
    payload: dict[str, Any] = {}
    if "temperature" in options:
        payload["temperature"] = options["temperature"]
    if "max_tokens" in options:
        payload["max_tokens"] = options["max_tokens"]
    return payload


def _request_policy(options: dict[str, Any] | None) -> OutboundRequestPolicy:
    total_timeout = float(
        (options or {}).get("timeout_seconds", DEFAULT_OUTBOUND_REQUEST_POLICY.total_timeout)
    )
    total_timeout = max(1.0, total_timeout)
    return OutboundRequestPolicy(
        connect_timeout=min(DEFAULT_OUTBOUND_REQUEST_POLICY.connect_timeout, total_timeout),
        read_timeout=total_timeout,
        write_timeout=min(DEFAULT_OUTBOUND_REQUEST_POLICY.write_timeout, total_timeout),
        pool_timeout=min(DEFAULT_OUTBOUND_REQUEST_POLICY.pool_timeout, total_timeout),
        total_timeout=total_timeout,
        max_response_bytes=_MAX_RESPONSE_BYTES,
    )


async def _iter_sse_data_lines(response) -> AsyncIterator[str]:
    buffer = b""
    async for chunk in response.aiter_bytes():
        buffer += chunk
        while b"\n" in buffer:
            raw_line, buffer = buffer.split(b"\n", 1)
            line = raw_line.rstrip(b"\r")
            if not line.startswith(b"data:"):
                continue
            yield line.removeprefix(b"data:").strip().decode("utf-8", "ignore")
    tail = buffer.rstrip(b"\r")
    if tail.startswith(b"data:"):
        yield tail.removeprefix(b"data:").strip().decode("utf-8", "ignore")
