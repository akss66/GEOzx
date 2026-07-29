import json
from typing import cast

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.adapters import CompletionResult
from app.orchestrator.brain_intelligence import BrainIntelligence
from app.orchestrator.capability_router import SkillUnavailable
from app.orchestrator.skills.registry import SkillRegistry
from app.schemas.conversation import TurnExecutionMode
from app.schemas.skills import SkillDefinition

TEST_SESSION = cast(AsyncSession, object())


class AccountInspectionInput(BaseModel):
    pass


class AccountInspectionReport(BaseModel):
    summary: str


@pytest.fixture
def registry() -> SkillRegistry:
    return SkillRegistry(
        [
            SkillDefinition(
                code="account_inspection",
                version=1,
                name="账号体检",
                description="诊断当前账号",
                supported_platforms=frozenset({"douyin"}),
                input_model=AccountInspectionInput,
                output_model=AccountInspectionReport,
                expert_codes=("06-operator",),
                tool_codes=("account.profile",),
                risk_level="low",
                approval_policy="none",
                artifact_type="account_inspection_report",
            )
        ]
    )


async def _classify(
    message: str,
    *,
    has_account: bool = True,
    requested_skill_code: str | None = None,
    registry: SkillRegistry | None = None,
):
    return await BrainIntelligence().classify_turn(
        TEST_SESSION,
        1,
        message,
        has_account=has_account,
        platform="douyin",
        requested_skill_code=requested_skill_code,
        registry=registry,
    )


@pytest.mark.asyncio
async def test_answer_turn_uses_operating_context_and_conversation_history(
    monkeypatch,
):
    captured: list[dict] = []

    async def fake_chat(_self, _session, _org_id, _agent_code, messages):
        captured.extend(messages)
        return (
            CompletionResult(
                "基于上一轮目标，我们可以继续拆解内容方向。",
                "test-model",
                4,
                8,
                12,
            ),
            0.0,
        )

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    answer = await BrainIntelligence().answer_turn(
        TEST_SESSION,
        1,
        "继续",
        operating_context="当前账号：测试账号。",
        history=[
            {"role": "user", "content": "我要提升咨询量"},
            {"role": "assistant", "content": "先分析高咨询内容。"},
        ],
        scope={"account_id": 3, "thread_id": 8, "turn_id": 12},
    )

    assert answer == "基于上一轮目标，我们可以继续拆解内容方向。"
    assert "当前账号：测试账号。" in captured[0]["content"]
    assert captured[-3:] == [
        {"role": "user", "content": "我要提升咨询量"},
        {"role": "assistant", "content": "先分析高咨询内容。"},
        {"role": "user", "content": "继续"},
    ]


@pytest.mark.asyncio
async def test_account_data_question_routes_to_query(monkeypatch):
    payload = {
        "mode": "query",
        "intent": "account_metrics",
        "confidence": 0.97,
        "reason": "只需读取账号数据",
        "skill_code": "account_data_query",
        "requires_account_context": True,
        "requires_operation_task": False,
        "missing_field": None,
        "clarifying_question": None,
    }

    async def fake_chat(*args, **kwargs):
        return CompletionResult(json.dumps(payload), "test-model", 4, 8, 12), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    decision = await _classify("查看我账号最近 30 天的数据")

    assert decision.mode is TurnExecutionMode.QUERY
    assert decision.requires_account_context is True
    assert decision.requires_operation_task is False


@pytest.mark.asyncio
async def test_casual_greeting_routes_to_answer_without_model(monkeypatch):
    async def fake_chat(*args, **kwargs):
        raise AssertionError("casual greeting must not call the model")

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    decision = await _classify("你好")

    assert decision.mode is TurnExecutionMode.ANSWER
    assert decision.requires_account_context is False
    assert decision.requires_operation_task is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "mode"),
    [
        ("制定 30 天策略", TurnExecutionMode.TASK),
        ("发布这条内容", TurnExecutionMode.ACTION),
    ],
)
async def test_durable_or_state_changing_turn_requires_operation_task(
    monkeypatch, message: str, mode: TurnExecutionMode
):
    payload = {
        "mode": mode.value,
        "intent": mode.value,
        "confidence": 0.9,
        "reason": "需要执行运营工作",
        "skill_code": None,
        "requires_account_context": True,
        "requires_operation_task": True,
        "missing_field": None,
        "clarifying_question": None,
    }

    async def fake_chat(*args, **kwargs):
        return CompletionResult(json.dumps(payload), "test-model", 4, 8, 12), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    decision = await _classify(message)

    assert decision.mode is mode
    assert decision.requires_operation_task is True


@pytest.mark.asyncio
async def test_explicit_compatible_skill_bypasses_model(monkeypatch, registry: SkillRegistry):
    async def fake_chat(*args, **kwargs):
        raise AssertionError("explicit Skill must not call the model")

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    decision = await _classify(
        "帮我做账号体检",
        requested_skill_code="account_inspection",
        registry=registry,
    )

    assert decision.mode is TurnExecutionMode.SKILL
    assert decision.skill_code == "account_inspection"


@pytest.mark.asyncio
async def test_explicit_skill_without_account_clarifies_without_model(
    monkeypatch, registry: SkillRegistry
):
    async def fake_chat(*args, **kwargs):
        raise AssertionError("explicit Skill must not call the model")

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    decision = await _classify(
        "帮我做账号体检",
        has_account=False,
        requested_skill_code="account_inspection",
        registry=registry,
    )

    assert decision.mode is TurnExecutionMode.CLARIFY
    assert decision.missing_field == "account_id"


@pytest.mark.asyncio
async def test_explicit_skill_requires_an_injected_registry():
    with pytest.raises(SkillUnavailable) as exc_info:
        await _classify(
            "帮我做账号体检",
            requested_skill_code="account_inspection",
        )

    assert exc_info.value.code == "skill_registry_unavailable"
    assert exc_info.value.reason == "explicit_skill_registry_required"


@pytest.mark.asyncio
async def test_account_required_model_route_is_converted_to_clarify(monkeypatch):
    payload = {
        "mode": "query",
        "intent": "account_metrics",
        "confidence": 0.97,
        "reason": "只需读取账号数据",
        "skill_code": "account_data_query",
        "requires_account_context": True,
        "requires_operation_task": False,
        "missing_field": None,
        "clarifying_question": None,
    }

    async def fake_chat(*args, **kwargs):
        return CompletionResult(json.dumps(payload), "test-model", 4, 8, 12), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    decision = await _classify("查看数据", has_account=False)

    assert decision.mode is TurnExecutionMode.CLARIFY
    assert decision.missing_field == "account_id"
    assert "账号" in decision.clarifying_question


@pytest.mark.asyncio
async def test_legacy_classify_keeps_intent_mapping(monkeypatch):
    payload = {
        "mode": "task",
        "intent": "content_strategy",
        "confidence": 0.9,
        "reason": "需要多步运营工作",
        "skill_code": None,
        "requires_account_context": True,
        "requires_operation_task": True,
        "missing_field": None,
        "clarifying_question": None,
    }

    async def fake_chat(*args, **kwargs):
        return CompletionResult(json.dumps(payload), "test-model", 4, 8, 12), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)

    decision = await BrainIntelligence().classify(
        TEST_SESSION, 1, "制定 30 天策略", has_account=True
    )

    assert decision.intent == "workflow"
    assert decision.requires_account_context is True
