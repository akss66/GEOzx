"""The deterministic adapter is an explicit test provider, never an HTTP bypass."""

from __future__ import annotations

import json

import pytest

from app.llm.adapters.deterministic_test import DeterministicTestAdapter


@pytest.mark.asyncio
async def test_deterministic_adapter_uses_contract_not_prompt_for_structured_route() -> None:
    adapter = DeterministicTestAdapter()
    result = await adapter.complete(
        "ci-model",
        [{"role": "user", "content": "arbitrary request text"}],
        {"response_format": {"type": "json_object"}},
    )

    decision = json.loads(result.content)
    assert decision["mode"] == "action"
    assert decision["requires_operation_task"] is True


@pytest.mark.asyncio
async def test_deterministic_adapter_uses_prompt_contract_for_runtime_next_step() -> None:
    adapter = DeterministicTestAdapter()
    result = await adapter.complete(
        "ci-model",
        [{"role": "user", "content": "arbitrary request text"}],
        {
            "response_format": {"type": "json_object"},
            "_deterministic_prompt_id": "main-agent.next-step",
        },
    )

    step = json.loads(result.content)
    assert step["action"] == "request_permission"
    assert step["tool_calls"] == [
        {
            "tool_code": "test.confirm_action",
            "arguments": {},
            "purpose": "Exercise the real approval persistence boundary.",
            "idempotency_key": "ci-confirm-action",
        }
    ]


@pytest.mark.asyncio
async def test_deterministic_adapter_streams_conversation_answer() -> None:
    adapter = DeterministicTestAdapter()
    chunks = [
        chunk
        async for chunk in adapter.stream(
            "ci-model",
            [{"role": "user", "content": "anything"}],
        )
    ]

    assert "".join(chunks) == "Operations Brain CI answer"
