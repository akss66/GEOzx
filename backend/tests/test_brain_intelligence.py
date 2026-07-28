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
        "mode": "clarify",
        "intent": "optimization_goal",
        "confidence": 0.82,
        "reason": "缺少需要优化的目标",
        "skill_code": None,
        "requires_account_context": True,
        "requires_operation_task": False,
        "missing_field": "optimization_goal",
        "clarifying_question": "你这次最想优先改善播放、互动，还是转化？",
    }

    captured: dict = {}

    async def fake_chat(_self, _session, _org_id, _agent_code, messages):
        captured["system"] = messages[0]["content"]
        captured["messages"] = messages
        captured["context"] = current_llm_call_context()
        return CompletionResult(json.dumps(payload), "test-model", 4, 8, 12), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    decision = await BrainIntelligence().classify(
        None,
        1,
        "帮我优化一下",
        has_account=True,
        platform="xiaohongshu",
    )

    assert decision.intent == "clarification"
    assert decision.clarifying_question == "你这次最想优先改善播放、互动，还是转化？"
    assert decision.suggested_expert_codes == []
    assert captured["system"].startswith("# 同行者主 Agent：本轮执行路由")
    assert "面向用户时统一使用“运营大脑”" in captured["system"]
    assert captured["context"].prompt_id == "main-agent.intent"
    assert captured["context"].prompt_schema_version == "turn-route-decision/v1"
    assert captured["context"].response_format == {"type": "json_object"}
    assert "当前平台：xiaohongshu" in captured["messages"][1]["content"]


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


@pytest.mark.asyncio
async def test_next_step_repairs_one_invalid_structured_response(monkeypatch):
    calls: list[list[dict]] = []
    payload = {
        "action": "dispatch_experts",
        "expert_codes": ["01-positioning"],
        "tool_calls": [],
        "rationale": "账号定位需要交给定位专家。",
        "handoff_message": "我先请账号定位专家完成诊断。",
        "decision_request": None,
        "purpose": "完成账号定位诊断",
        "evidence_refs": ["selected-account"],
    }

    async def fake_chat(_self, _session, _org_id, _agent_code, messages):
        calls.append(messages)
        content = "not-json" if len(calls) == 1 else json.dumps(payload)
        return CompletionResult(content, "test-model", 4, 8, 12), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    step = await BrainIntelligence().decide_next(
        None,
        1,
        "完成账号定位诊断",
        [],
        ["01-positioning"],
        1,
    )

    assert step.action == "dispatch_experts"
    assert [code.value for code in step.expert_codes] == ["01-positioning"]
    assert len(calls) == 2
    assert calls[1][-2]["role"] == "assistant"
    assert calls[1][-2]["content"] == "not-json"
    assert "仅输出修正后的唯一 JSON 对象" in calls[1][-1]["content"]
