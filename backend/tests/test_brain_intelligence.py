import json

import pytest

from app.llm.adapters import CompletionResult
from app.llm.gateway import current_llm_call_context
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

    captured: dict = {}

    async def fake_chat(_self, _session, _org_id, _agent_code, messages):
        captured["system"] = messages[0]["content"]
        captured["context"] = current_llm_call_context()
        return CompletionResult(json.dumps(payload), "test-model", 4, 8, 12), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    decision = await BrainIntelligence().classify(None, 1, "帮我优化一下", has_account=True)

    assert decision.intent == "clarification"
    assert decision.clarifying_question == "你这次最想优先改善播放、互动，还是转化？"
    assert decision.suggested_expert_codes == []
    assert captured["system"].startswith("# 同舟行主 Agent：意图路由")
    assert "面向用户时统一使用“运营大脑”" in captured["system"]
    assert captured["context"].prompt_id == "main-agent.intent"
    assert captured["context"].prompt_schema_version == "intent-decision/v1"
    assert captured["context"].response_format == {"type": "json_object"}


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


@pytest.mark.asyncio
async def test_next_step_can_request_scoped_tool_calls(monkeypatch):
    system_prompts: list[str] = []
    payload = {
        "action": "call_tools",
        "expert_codes": [],
        "tool_calls": [
            {
                "tool_code": "account.metrics_summary",
                "arguments": {"days": 30},
                "purpose": "读取当前账号近 30 天真实表现",
                "idempotency_key": "metrics-round-1",
            }
        ],
        "rationale": "需要先读取真实数据再判断",
        "handoff_message": "我先读取当前账号的近期数据。",
        "decision_request": None,
        "purpose": "补齐表现证据",
        "evidence_refs": ["selected-account"],
    }

    async def fake_chat(_self, _session, _org_id, _agent_code, messages):
        system_prompts.append(messages[0]["content"])
        return CompletionResult(json.dumps(payload), "test-model", 4, 8, 12), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    step = await BrainIntelligence().decide_next(
        None,
        1,
        "复盘当前账号",
        [],
        [
            {
                "kind": "tool",
                "code": "account.metrics_summary",
                "description": "读取当前账号指标",
            }
        ],
        1,
    )

    assert step.action == "call_tools"
    assert step.tool_calls[0].tool_code == "account.metrics_summary"
    assert step.tool_calls[0].arguments == {"days": 30}
    assert "call_tools" in system_prompts[0]
    assert "idempotency_key" in system_prompts[0]
    assert system_prompts[0].startswith("# 同舟行主 Agent：受控 ReAct 下一步")
    assert "面向用户时统一使用“运营大脑”" in system_prompts[0]
    assert "account.metrics_summary" in system_prompts[0]
