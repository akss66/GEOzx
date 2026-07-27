"""Deterministic budget and loop guards for the main-agent ReAct runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class RuntimeBudgetLimits:
    max_rounds: int = 8
    max_expert_calls: int = 12
    max_expert_calls_per_code: int = 3
    max_tool_calls: int = 20
    max_tokens: int = 100_000
    max_cost_usd: float = 5.0
    max_elapsed_seconds: int = 900


@dataclass(frozen=True)
class ExpertAuthorization:
    allowed_codes: list[str]
    state: dict[str, Any]
    blocked_reason: str | None = None


@dataclass(frozen=True)
class ToolAuthorization:
    allowed_count: int
    state: dict[str, Any]
    blocked_reason: str | None = None


class RuntimeBudgetGuard:
    """Apply persisted, replay-safe limits before dispatching side effects."""

    def __init__(self, limits: RuntimeBudgetLimits | None = None) -> None:
        self.limits = limits or RuntimeBudgetLimits()

    def exhaustion_reason(
        self,
        state: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> str | None:
        if int(state.get("round_index", 1)) > self.limits.max_rounds:
            return "round_budget_exhausted"
        if int(state.get("token_count", 0)) > self.limits.max_tokens:
            return "token_budget_exhausted"
        if float(state.get("cost_usd", 0.0)) > self.limits.max_cost_usd:
            return "cost_budget_exhausted"

        started_at = state.get("runtime_started_at")
        if started_at:
            try:
                started = datetime.fromisoformat(str(started_at))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=UTC)
            except ValueError:
                return "invalid_runtime_started_at"
            current = now or datetime.now(UTC)
            if (current - started).total_seconds() > self.limits.max_elapsed_seconds:
                return "elapsed_time_budget_exhausted"
        return None

    def authorize_experts(
        self,
        state: Mapping[str, Any],
        codes: list[str],
        *,
        purpose: str | None,
        evidence_refs: list[str],
    ) -> ExpertAuthorization:
        current = dict(state)
        exhausted = self.exhaustion_reason(current)
        if exhausted is not None:
            return ExpertAuthorization([], current, exhausted)

        history = [dict(item) for item in current.get("expert_dispatch_history", [])]
        accepted: list[str] = []
        blocked_reasons: list[str] = []
        for code in dict.fromkeys(codes):
            if len(history) >= self.limits.max_expert_calls:
                blocked_reasons.append("expert_call_budget_exhausted")
                break
            prior_for_code = [item for item in history if item.get("agent_code") == code]
            if len(prior_for_code) >= self.limits.max_expert_calls_per_code:
                blocked_reasons.append("expert_per_code_budget_exhausted")
                continue

            signature = expert_dispatch_signature(code, purpose, evidence_refs)
            if any(item.get("signature") == signature for item in prior_for_code):
                blocked_reasons.append("duplicate_expert_dispatch")
                continue

            history.append(
                {
                    "agent_code": code,
                    "purpose": _normalize_text(purpose),
                    "evidence_refs": _normalize_evidence(evidence_refs),
                    "signature": signature,
                    "dispatched_at": datetime.now(UTC).isoformat(),
                }
            )
            accepted.append(code)

        current["expert_dispatch_history"] = history
        reason = blocked_reasons[0] if not accepted and blocked_reasons else None
        return ExpertAuthorization(accepted, current, reason)

    def authorize_tools(
        self,
        state: Mapping[str, Any],
        requested_count: int,
    ) -> ToolAuthorization:
        current = dict(state)
        exhausted = self.exhaustion_reason(current)
        if exhausted is not None:
            return ToolAuthorization(0, current, exhausted)
        existing = int(current.get("tool_call_count", 0))
        if requested_count < 1:
            return ToolAuthorization(0, current, "empty_tool_call_batch")
        if existing + requested_count > self.limits.max_tool_calls:
            return ToolAuthorization(0, current, "tool_call_budget_exhausted")
        current["tool_call_count"] = existing + requested_count
        return ToolAuthorization(requested_count, current)


def expert_dispatch_signature(
    code: str,
    purpose: str | None,
    evidence_refs: list[str],
) -> str:
    payload = {
        "agent_code": code,
        "purpose": _normalize_text(purpose),
        "evidence_refs": _normalize_evidence(evidence_refs),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _normalize_evidence(values: list[str]) -> list[str]:
    return sorted(
        {
            " ".join(str(value).split()).casefold()
            for value in values
            if str(value).strip()
        }
    )
