"""LLMAgent 基座测试：JSON 抽取、schema 校验、失败重试（fake 网关，不触网）。"""

import pytest

from app.agents.base import AgentContext, LLMAgent, extract_json
from app.llm.adapters import CompletionResult
from app.models.enums import DeliverableType
from app.schemas.deliverable import PositioningStrategyPayload

_VALID = {
    "account_persona": "硬核数码测评",
    "target_audience": "25-35 岁科技爱好者",
    "differentiation": ["真机长测", "深度拆解"],
    "content_pillars": ["新品首发", "横向对比"],
}


class FakeGateway:
    """按预置脚本依次返回 content；记录调用次数。"""

    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.calls = 0

    async def chat(self, session, org_id, agent_code, messages):
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
    assert extract_json('前言\n```json\n{"a": 1}\n```\n结语') == {"a": 1}


def test_extract_json_embedded():
    assert extract_json('结果是 {"a": 1, "b": [2,3]} 完毕') == {"a": 1, "b": [2, 3]}


def test_extract_json_invalid_raises():
    with pytest.raises(ValueError):
        extract_json("这里没有 JSON")


@pytest.mark.asyncio
async def test_llm_agent_valid_json(monkeypatch):
    import json

    agent = _PositioningAgent(llm=FakeGateway([json.dumps(_VALID)]))
    result = await agent.run(None, 1, AgentContext(content_item_id=1))
    assert isinstance(result, PositioningStrategyPayload)
    assert result.account_persona == "硬核数码测评"


@pytest.mark.asyncio
async def test_llm_agent_fenced_json(monkeypatch):
    import json

    fenced = f"这是结果：\n```json\n{json.dumps(_VALID)}\n```"
    agent = _PositioningAgent(llm=FakeGateway([fenced]))
    result = await agent.run(None, 1, AgentContext(content_item_id=1))
    assert result.differentiation == ["真机长测", "深度拆解"]


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
