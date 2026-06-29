"""编排引擎测试：流转 / 版本化 / 质量门阻塞与审批（fake emit + fake LLM，SQLite）。"""

import json

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.llm.adapters import CompletionResult
from app.models import AgentTask, ContentItem, Deliverable, GateApproval, Org, Project
from app.models.enums import (
    AgentTaskStatus,
    ContentStage,
    ContentStatus,
    DeliverableType,
    GateStatus,
)
from app.orchestrator.engine import OrchestrationEngine

# 各 Agent 真实输出形状的占位（满足对应 payload schema）。
_POSITIONING_JSON = json.dumps(
    {
        "account_persona": "硬核数码测评",
        "target_audience": "25-35 岁科技爱好者",
        "differentiation": ["真机长测", "深度拆解"],
        "content_pillars": ["新品首发", "横向对比"],
    }
)

_SCRIPT_JSON = json.dumps(
    {
        "title": "新品开箱：三分钟看懂值不值",
        "hook": "这台机器，贵的有道理吗？",
        "scenes": ["开箱", "上手实测", "结论"],
        "duration_seconds": 45,
        "bgm_suggestion": "轻快电子",
    }
)

_AGENT_OUTPUT = {"01-positioning": _POSITIONING_JSON, "02-content": _SCRIPT_JSON}


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    """拦截真实网关调用：PIPELINE 各步是真实 LLMAgent，测试不触网。按 agent_code 返回对应形状。"""

    async def fake_chat(self, session, org_id, agent_code, messages):
        content = _AGENT_OUTPUT.get(agent_code, _POSITIONING_JSON)
        return CompletionResult(content, "deepseek-chat", 10, 20, 30), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)


class FakeEmit:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def __call__(
        self, event_type, payload=None, content_item_id=None, project_id=None
    ) -> None:
        self.events.append(event_type)


@pytest_asyncio.fixture
async def content_item(session) -> ContentItem:
    org = Org(name="O")
    project = Project(org=org, name="P")
    ci = ContentItem(project=project, title="测试内容")
    session.add(ci)
    await session.commit()
    await session.refresh(ci)
    return ci


async def _pending_gate(session, ci_id) -> GateApproval:
    return await session.scalar(
        select(GateApproval).where(
            GateApproval.content_item_id == ci_id,
            GateApproval.status == GateStatus.PENDING,
        )
    )


@pytest.mark.asyncio
async def test_pipeline_runs_until_forced_gate(session, content_item) -> None:
    emit = FakeEmit()
    engine = OrchestrationEngine(emit=emit)
    await engine.start(session, content_item.id)
    await session.refresh(content_item)

    # 两个 Agent 步都完成
    tasks = (
        await session.scalars(select(AgentTask).where(AgentTask.content_item_id == content_item.id))
    ).all()
    assert {t.stage for t in tasks} == {
        ContentStage.POSITIONING,
        ContentStage.CONTENT_DIRECTION,
    }
    assert all(t.status == AgentTaskStatus.DONE for t in tasks)

    # 两份版本化交付物（version=1）
    delivs = (
        await session.scalars(
            select(Deliverable).where(Deliverable.content_item_id == content_item.id)
        )
    ).all()
    assert {d.type for d in delivs} == {
        DeliverableType.POSITIONING_STRATEGY,
        DeliverableType.VIDEO_SCRIPT,
    }
    assert all(d.version == 1 for d in delivs)

    # 自动门 auto_passed + 强制门 pending；内容 BLOCKED
    gates = (
        await session.scalars(
            select(GateApproval).where(GateApproval.content_item_id == content_item.id)
        )
    ).all()
    assert sorted(g.status.value for g in gates) == ["auto_passed", "pending"]
    assert content_item.status == ContentStatus.BLOCKED
    assert "gate.pending" in emit.events
    assert "agent.done" in emit.events


@pytest.mark.asyncio
async def test_approve_forced_gate_completes_pipeline(session, content_item) -> None:
    emit = FakeEmit()
    engine = OrchestrationEngine(emit=emit)
    await engine.start(session, content_item.id)

    gate = await _pending_gate(session, content_item.id)
    await engine.approve_gate(session, gate.id, user_id=1, approved=True)

    await session.refresh(content_item)
    await session.refresh(gate)
    assert gate.status == GateStatus.APPROVED
    assert content_item.status == ContentStatus.PUBLISHED
    assert "pipeline.done" in emit.events


@pytest.mark.asyncio
async def test_reject_forced_gate_stays_blocked(session, content_item) -> None:
    engine = OrchestrationEngine(emit=FakeEmit())
    await engine.start(session, content_item.id)

    gate = await _pending_gate(session, content_item.id)
    await engine.approve_gate(session, gate.id, user_id=1, approved=False, comment="不合规")

    await session.refresh(content_item)
    await session.refresh(gate)
    assert gate.status == GateStatus.REJECTED
    assert content_item.status == ContentStatus.BLOCKED


@pytest.mark.asyncio
async def test_advance_is_idempotent_while_blocked(session, content_item) -> None:
    engine = OrchestrationEngine(emit=FakeEmit())
    await engine.start(session, content_item.id)
    # 再次推进（阻塞中）不应重复建任务/门
    await engine.advance(session, content_item)

    tasks = (
        await session.scalars(select(AgentTask).where(AgentTask.content_item_id == content_item.id))
    ).all()
    gates = (
        await session.scalars(
            select(GateApproval).where(GateApproval.content_item_id == content_item.id)
        )
    ).all()
    assert len(tasks) == 2
    assert len(gates) == 2  # 1 auto_passed + 1 pending，无重复


@pytest.mark.asyncio
async def test_content_agent_receives_upstream_positioning(session, content_item, monkeypatch):
    """验证上游定位交付物流转到编导 Agent 的输入（messages 含定位 payload）。"""
    captured: dict[str, list] = {}

    async def capturing_chat(self, sess, org_id, agent_code, messages):
        captured[agent_code] = messages
        return CompletionResult(_AGENT_OUTPUT[agent_code], "deepseek-chat", 10, 20, 30), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", capturing_chat)

    engine = OrchestrationEngine(emit=FakeEmit())
    await engine.start(session, content_item.id)

    # 编导 Agent 的 user 消息里应含上游定位策略的关键字段
    content_user_msg = next(
        m["content"] for m in captured["02-content"] if m["role"] == "user"
    )
    assert "positioning_strategy" in content_user_msg
    assert "硬核数码测评" in content_user_msg  # 来自定位 Agent 的输出
    # 定位 Agent（首步）无上游
    positioning_user_msg = next(
        m["content"] for m in captured["01-positioning"] if m["role"] == "user"
    )
    assert "无上游输入" in positioning_user_msg
