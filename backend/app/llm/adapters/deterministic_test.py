"""Deterministic model provider available only to explicit test environments."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.llm.adapters import CompletionResult


class DeterministicTestAdapter:
    provider = "deterministic_test"

    async def complete(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> CompletionResult:
        del messages
        effective_options = options or {}
        if effective_options.get("_deterministic_prompt_id") == "main-agent.next-step":
            payload = {
                "action": "request_permission",
                "expert_codes": [],
                "rationale": "The requested action crosses an approval boundary.",
                "handoff_message": "Approval is required before this action can continue.",
                "decision_request": None,
                "tool_calls": [
                    {
                        "tool_code": "test.confirm_action",
                        "arguments": {},
                        "purpose": "Exercise the real approval persistence boundary.",
                        "idempotency_key": "ci-confirm-action",
                    }
                ],
                "purpose": "Verify the real approval boundary.",
                "evidence_refs": ["ci-real-api-smoke"],
            }
        else:
            payload = (
                {
                    "mode": "action",
                    "intent": "publish_action",
                    "confidence": 1,
                    "reason": (
                        "CI deterministic provider classified a controlled external action."
                    ),
                    "skill_code": None,
                    "requires_account_context": True,
                    "requires_operation_task": True,
                    "missing_field": None,
                    "clarifying_question": None,
                }
                if effective_options.get("response_format") == {"type": "json_object"}
                else None
            )
        content = (
            json.dumps(payload, ensure_ascii=False)
            if payload is not None
            else "Operations Brain CI answer"
        )
        return CompletionResult(content, model, 4, 6, 10)

    async def stream(
        self,
        model: str,
        messages: list[dict],
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        del model, messages, options
        yield "Operations Brain "
        yield "CI answer"
