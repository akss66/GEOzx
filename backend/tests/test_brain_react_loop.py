from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.orchestrator.runtime_budget import RuntimeBudgetGuard, RuntimeBudgetLimits
from app.schemas.brain import RuntimeNextStep, RuntimeToolCall


def _state(**overrides):
    state = {
        "round_index": 1,
        "runtime_started_at": datetime.now(UTC).isoformat(),
        "expert_dispatch_history": [],
        "tool_call_count": 0,
        "token_count": 0,
        "cost_usd": 0.0,
    }
    state.update(overrides)
    return state


def test_same_expert_requires_new_purpose_or_evidence_before_repeat():
    guard = RuntimeBudgetGuard(RuntimeBudgetLimits(max_expert_calls_per_code=3))
    state = _state()

    first = guard.authorize_experts(
        state,
        ["01-positioning"],
        purpose="核对账号定位",
        evidence_refs=["account-profile:1"],
    )
    repeated = guard.authorize_experts(
        first.state,
        ["01-positioning"],
        purpose="核对账号定位",
        evidence_refs=["account-profile:1"],
    )
    revised = guard.authorize_experts(
        first.state,
        ["01-positioning"],
        purpose="根据新作品数据修正定位",
        evidence_refs=["metrics-snapshot:2"],
    )

    assert first.allowed_codes == ["01-positioning"]
    assert repeated.allowed_codes == []
    assert repeated.blocked_reason == "duplicate_expert_dispatch"
    assert revised.allowed_codes == ["01-positioning"]
    assert len(revised.state["expert_dispatch_history"]) == 2


def test_expert_and_tool_budgets_are_enforced_before_dispatch():
    guard = RuntimeBudgetGuard(
        RuntimeBudgetLimits(
            max_expert_calls=2,
            max_expert_calls_per_code=1,
            max_tool_calls=2,
        )
    )
    first = guard.authorize_experts(
        _state(),
        ["01-positioning", "02-content-director"],
        purpose="形成定位和内容方向",
        evidence_refs=[],
    )
    blocked_expert = guard.authorize_experts(
        first.state,
        ["03-art-director"],
        purpose="继续生成视觉方向",
        evidence_refs=[],
    )
    tools = guard.authorize_tools(first.state, 2)
    blocked_tools = guard.authorize_tools(tools.state, 1)

    assert blocked_expert.allowed_codes == []
    assert blocked_expert.blocked_reason == "expert_call_budget_exhausted"
    assert tools.allowed_count == 2
    assert blocked_tools.allowed_count == 0
    assert blocked_tools.blocked_reason == "tool_call_budget_exhausted"


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (_state(round_index=9), "round_budget_exhausted"),
        (_state(token_count=100_001), "token_budget_exhausted"),
        (_state(cost_usd=5.01), "cost_budget_exhausted"),
        (
            _state(
                runtime_started_at=(datetime.now(UTC) - timedelta(seconds=901)).isoformat()
            ),
            "elapsed_time_budget_exhausted",
        ),
    ],
)
def test_runtime_budget_reports_terminal_reason(state, reason):
    guard = RuntimeBudgetGuard(
        RuntimeBudgetLimits(
            max_rounds=8,
            max_tokens=100_000,
            max_cost_usd=5,
            max_elapsed_seconds=900,
        )
    )

    assert guard.exhaustion_reason(state) == reason


def test_request_permission_requires_a_concrete_tool_call():
    with pytest.raises(ValidationError):
        RuntimeNextStep(
            action="request_permission",
            rationale="需要执行受控动作",
            handoff_message="需要你确认后继续。",
        )

    step = RuntimeNextStep(
        action="request_permission",
        rationale="需要执行受控动作",
        handoff_message="需要你确认后继续。",
        tool_calls=[
            RuntimeToolCall(
                tool_code="publish.prepare",
                arguments={},
                purpose="生成发布包",
                idempotency_key="publish-package-1",
            )
        ],
    )

    assert step.action == "request_permission"
