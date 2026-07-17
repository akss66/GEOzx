import json

import pytest

from app.llm.adapters import CompletionResult
from app.orchestrator.brain_intelligence import (
    BrainIntelligence,
    IntelligenceUnavailable,
)


@pytest.mark.asyncio
async def test_greeting_does_not_call_model_or_dispatch_experts(monkeypatch):
    calls = 0

    async def fake_chat(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("问候不应调用规划模型")

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    decision = await BrainIntelligence().classify(None, 1, "你好", has_account=True)

    assert decision.intent == "conversation"
    assert decision.suggested_expert_codes == []
    assert decision.clarifying_question is None
    assert calls == 0


@pytest.mark.asyncio
async def test_ambiguous_goal_asks_exactly_one_question(monkeypatch):
    payload = {
        "intent": "clarification",
        "confidence": 0.82,
        "reason": "缺少需要优化的目标",
        "missing_field": "optimization_goal",
        "clarifying_question": "你这次最想优先改善播放、互动，还是转化？",
        "suggested_expert_codes": [],
        "requires_account_context": True,
    }

    async def fake_chat(*args, **kwargs):
        return CompletionResult(json.dumps(payload), "test-model", 4, 8, 12), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    decision = await BrainIntelligence().classify(None, 1, "帮我优化一下", has_account=True)

    assert decision.intent == "clarification"
    assert decision.clarifying_question == "你这次最想优先改善播放、互动，还是转化？"
    assert decision.suggested_expert_codes == []


@pytest.mark.asyncio
async def test_invalid_model_decision_never_falls_back_to_fixed_experts(monkeypatch):
    async def fake_chat(*args, **kwargs):
        return CompletionResult("not-json", "test-model", 4, 8, 12), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    with pytest.raises(IntelligenceUnavailable):
        await BrainIntelligence().classify(
            None,
            1,
            "分析当前账号并制定下周内容策略",
            has_account=True,
        )


@pytest.mark.asyncio
async def test_next_step_can_finish_after_observing_an_expert(monkeypatch):
    payload = {
        "action": "finish",
        "expert_codes": [],
        "rationale": "定位结论已经足以回答目标",
        "handoff_message": "定位已经明确，我来为你汇总结论。",
        "decision_request": None,
    }

    async def fake_chat(*args, **kwargs):
        return CompletionResult(json.dumps(payload), "test-model", 4, 8, 12), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    step = await BrainIntelligence().decide_next(
        None,
        1,
        "分析账号定位",
        [{"agent_code": "01-positioning", "summary": "核心定位已经明确"}],
        ["01-positioning", "06-operation"],
        1,
    )

    assert step.action == "finish"
    assert step.expert_codes == []
    assert step.handoff_message == "定位已经明确，我来为你汇总结论。"
