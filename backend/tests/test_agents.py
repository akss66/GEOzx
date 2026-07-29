"""LLMAgent 基座测试：严格 JSON、版本 Prompt、schema 校验与有界修复。"""

import pytest

from app.agents.base import AgentContext, LLMAgent, extract_json
from app.agents.registry import AGENT_SPECS
from app.llm.adapters import CompletionResult
from app.llm.gateway import current_llm_call_context
from app.models.enums import AgentCode, DeliverableType
from app.orchestrator.agent_kernel import KernelAction
from app.schemas.deliverable import PositioningStrategyPayload

_VALID = {
    "account_persona": "硬核数码测评",
    "target_audience": "25-35 岁科技爱好者",
    "differentiation": ["真机长测", "深度拆解"],
    "content_pillars": ["新品首发", "横向对比"],
}

_VALID_EXPERT_PAYLOADS = {
    AgentCode.POSITIONING: _VALID,
    AgentCode.CONTENT_DIRECTOR: {
        "title": "真实体验复盘",
        "hook": "这次测试结果和预期完全不同",
        "scenes": ["展示问题", "解释原因", "给出结论"],
        "duration_seconds": 45,
        "bgm_suggestion": "克制、清晰",
    },
    AgentCode.ART_DIRECTOR: {
        "visual_style": "真实产品实验室",
        "prompts": ["近景展示产品细节", "俯拍展示测试过程"],
        "negative_prompt": "过度磨皮、虚假参数",
        "aspect_ratio": "9:16",
    },
    AgentCode.VIDEO_CREATOR: {
        "tool": "planned",
        "clips": [{"scene": "测试过程", "duration_seconds": 5, "prompt": "真实光线"}],
        "resolution": "1080x1920",
        "notes": "保持镜头连续",
        "video_url": None,
        "gen_task_id": None,
        "gen_status": None,
    },
    AgentCode.EDITOR: {
        "cut_plan": ["0-3 秒呈现结论"],
        "captions": ["突出关键参数"],
        "transitions": "只使用硬切",
        "deliverables": ["1080x1920 MP4"],
        "platform_variants": ["抖音版保留前三秒结论"],
    },
    AgentCode.OPERATOR: {
        "period": "近 7 天",
        "summary": "真实测评内容互动更稳定",
        "key_metrics": {"engagement_rate": "5.2%"},
        "highlights": ["评论问题集中"],
        "issues": ["开场节奏偏慢"],
        "optimization_suggestions": ["缩短开场并观察完播率"],
    },
    AgentCode.ADVERTISER: {
        "objective": "验证内容方向",
        "target_audience": "理性数码消费者",
        "budget_strategy": "小额验证，达到阈值后扩量",
        "creative_directions": ["真实体验", "同价位对比"],
        "risk_controls": ["人工确认预算", "达到止损线即停止"],
        "measurement": {"primary_metric": "有效互动成本"},
    },
    AgentCode.CUSTOMER_SERVICE: {
        "period": "近 7 天",
        "summary": "用户集中关注续航表现",
        "common_questions": ["高负载续航多久"],
        "sentiment": {"overall": "中性", "evidence": "以参数询问为主"},
        "response_guidelines": ["只引用实测数据"],
        "content_opportunities": ["制作高负载续航实测"],
    },
}


class FakeGateway:
    """按预置脚本依次返回 content；记录调用次数。"""

    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.calls = 0
        self.messages: list[list[dict]] = []
        self.contexts = []

    async def chat(self, session, org_id, agent_code, messages):
        self.messages.append(messages.copy())
        self.contexts.append(current_llm_call_context())
        content = self._contents[min(self.calls, len(self._contents) - 1)]
        self.calls += 1
        return CompletionResult(content, "deepseek-chat", 10, 20, 30), 0.0


class _PositioningAgent(LLMAgent):
    code = "01-positioning"
    output_type = DeliverableType.POSITIONING_STRATEGY
    prompt_name = "01-positioning"


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    with pytest.raises(ValueError):
        extract_json('```json\n{"a": 1}\n```')


def test_extract_json_embedded():
    with pytest.raises(ValueError):
        extract_json('结果是 {"a": 1, "b": [2,3]} 完毕')


def test_extract_json_invalid_raises():
    with pytest.raises(ValueError):
        extract_json("这里没有 JSON")


@pytest.mark.asyncio
async def test_llm_agent_valid_json(monkeypatch):
    import json

    llm = FakeGateway([json.dumps(_VALID)])
    agent = _PositioningAgent(llm=llm)
    result = await agent.run(
        None,
        1,
        AgentContext(
            content_item_id=1,
            task_id=41,
            invocation_id=73,
            trace_id="run-5:step-2",
            project_id=9,
            account_id=12,
            budget={"max_tokens": 2048},
        ),
    )
    assert isinstance(result, PositioningStrategyPayload)
    assert result.account_persona == "硬核数码测评"
    assert llm.messages[0][0]["content"].startswith("# 账号定位专家")
    assert "TODO" not in llm.messages[0][0]["content"]
    call_context = llm.contexts[0]
    assert call_context.prompt_id == "expert.01-positioning"
    assert call_context.prompt_version == "1.0.0"
    assert call_context.prompt_hash == (
        "9903020ab775fa6999943b83ca085587265ff97f3ceae31118750cc4e5bc45f7"
    )
    assert call_context.prompt_schema_version == "positioning-strategy/v1"
    assert call_context.task_id == 41
    assert call_context.invocation_id == 73
    assert call_context.trace_id == "run-5:step-2"
    assert call_context.scope == {"org_id": 1, "project_id": 9, "account_id": 12}
    assert call_context.budget == {"max_tokens": 2048}
    assert call_context.response_format == {"type": "json_object"}


def test_agent_context_accepts_ordered_expert_outputs() -> None:
    context = AgentContext(
        content_item_id=1,
        upstream={
            "expert_outputs": [
                {
                    "agent_code": "06-operator",
                    "output": {"summary": "运营诊断"},
                }
            ]
        },
    )

    assert context.upstream["expert_outputs"][0]["agent_code"] == "06-operator"


@pytest.mark.asyncio
async def test_llm_agent_kernel_accepts_legacy_direct_deliverable() -> None:
    import json

    gateway = FakeGateway([json.dumps(_VALID)])
    agent = _PositioningAgent(llm=gateway)

    decision = await agent.kernel_decide(
        None,
        1,
        AgentContext(content_item_id=1, task_id=2, invocation_id=3),
        available_tools=[{"code": "account.profile"}],
        observations=[],
    )

    assert decision.action == KernelAction.FINISH
    assert isinstance(decision.deliverable, PositioningStrategyPayload)
    assert decision.deliverable.account_persona == "硬核数码测评"


@pytest.mark.asyncio
async def test_llm_agent_kernel_parses_tool_call_and_owns_idempotency_key() -> None:
    import json

    gateway = FakeGateway(
        [
            json.dumps(
                {
                    "action": "call_tools",
                    "rationale": "Need selected account evidence.",
                    "tool_calls": [
                        {
                            "tool_code": "account.profile",
                            "arguments": {},
                            "purpose": "Read current account profile.",
                            "idempotency_key": "model-controlled-key",
                        }
                    ],
                }
            )
        ]
    )
    agent = _PositioningAgent(llm=gateway)

    decision = await agent.kernel_decide(
        None,
        1,
        AgentContext(content_item_id=1, task_id=2, invocation_id=73),
        available_tools=[{"code": "account.profile"}],
        observations=[],
    )

    assert decision.action == KernelAction.CALL_TOOLS
    assert decision.tool_calls[0].tool_code == "account.profile"
    assert decision.tool_calls[0].idempotency_key.startswith("expert:73:")
    assert decision.tool_calls[0].idempotency_key != "model-controlled-key"


@pytest.mark.asyncio
async def test_llm_agent_repairs_non_structured_output_once(monkeypatch):
    import json

    fenced = f"这是结果：\n```json\n{json.dumps(_VALID)}\n```"
    llm = FakeGateway([fenced, json.dumps(_VALID)])
    agent = _PositioningAgent(llm=llm)
    result = await agent.run(None, 1, AgentContext(content_item_id=1))
    assert result.differentiation == ["真机长测", "深度拆解"]
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_llm_agent_retries_on_bad_then_succeeds():
    import json

    gw = FakeGateway(["不是 JSON", json.dumps(_VALID)])
    agent = _PositioningAgent(llm=gw)
    result = await agent.run(None, 1, AgentContext(content_item_id=1))
    assert result.account_persona == "硬核数码测评"
    assert gw.calls == 2  # 第一次失败、重试第二次成功


@pytest.mark.asyncio
async def test_llm_agent_raises_after_exhausting_retries():
    # 始终返回结构不符的 JSON（缺字段）→ 重试用尽后抛错
    gw = FakeGateway(['{"account_persona": "x"}'])
    agent = _PositioningAgent(llm=gw)
    with pytest.raises(ValueError):
        await agent.run(None, 1, AgentContext(content_item_id=1))
    assert gw.calls == agent.max_retries + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_code", list(AGENT_SPECS))
async def test_every_specialist_has_a_versioned_prompt_and_valid_output_contract(agent_code):
    import json

    spec = AGENT_SPECS[agent_code]
    gateway = FakeGateway([json.dumps(_VALID_EXPERT_PAYLOADS[agent_code])])
    agent = spec.runner(llm=gateway)

    payload = await agent.run(
        None,
        7,
        AgentContext(
            content_item_id=1,
            task_id=2,
            invocation_id=3,
            trace_id="contract-eval",
            project_id=4,
            account_id=5,
        ),
    )

    assert payload.model_dump(mode="json")
    assert gateway.calls == 1
    assert gateway.contexts[0].prompt_id == f"expert.{agent.prompt_name}"
    assert gateway.contexts[0].prompt_version == "1.0.0"
    assert gateway.contexts[0].prompt_schema_version
