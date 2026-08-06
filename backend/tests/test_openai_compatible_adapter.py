from contextlib import asynccontextmanager

import httpx
import pytest

from app.llm.adapters.openai_compatible import OpenAICompatibleAdapter


@pytest.mark.asyncio
async def test_complete_allows_mixed_dns_only_when_adapter_is_explicitly_trusted(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_request(method: str, url: str, **options):
        captured.update(method=method, url=url, **options)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(
        "app.llm.adapters.openai_compatible.bounded_outbound_request",
        fake_request,
    )
    adapter = OpenAICompatibleAdapter(
        provider_code="deepseek",
        api_key="test-key",
        base_url="https://api.deepseek.com",
        allow_mixed_dns=True,
    )

    result = await adapter.complete("deepseek-chat", [{"role": "user", "content": "hi"}])

    assert result.content == "ok"
    assert captured["_allow_mixed_dns"] is True


@pytest.mark.asyncio
async def test_stream_preserves_explicit_mixed_dns_policy(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeStream:
        status_code = 200

        async def aiter_bytes(self):
            yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b"data: [DONE]\n\n"

    @asynccontextmanager
    async def fake_stream(method: str, url: str, **options):
        captured.update(method=method, url=url, **options)
        yield FakeStream()

    monkeypatch.setattr(
        "app.llm.adapters.openai_compatible.bounded_outbound_stream",
        fake_stream,
    )
    adapter = OpenAICompatibleAdapter(
        provider_code="deepseek",
        api_key="test-key",
        base_url="https://api.deepseek.com",
        allow_mixed_dns=True,
    )

    chunks = [
        chunk
        async for chunk in adapter.stream(
            "deepseek-chat",
            [{"role": "user", "content": "hi"}],
        )
    ]

    assert chunks == ["ok"]
    assert captured["_allow_mixed_dns"] is True
