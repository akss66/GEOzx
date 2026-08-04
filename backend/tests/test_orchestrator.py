"""编排引擎测试：流转 / 版本化 / 质量门阻塞与审批（fake emit + fake LLM，SQLite）。"""

import json

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.llm.adapters import CompletionResult
from app.models import (
    AgentTask,
    Client,
    ContentItem,
    Deliverable,
    Event,
    GateApproval,
    KnowledgeEntry,
    Org,
    Project,
)
from app.models.enums import (
    AgentTaskStatus,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
    GateStatus,
    GateType,
    KnowledgeCategory,
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

_ART_JSON = json.dumps(
    {
        "visual_style": "硬核冷调科技风",
        "prompts": ["开箱特写，冷光", "上手实测，俯拍"],
        "aspect_ratio": "9:16",
    }
)

_VIDEO_JSON = json.dumps(
    {
        "tool": "seedance",
        "clips": [{"prompt": "开箱", "duration_seconds": 5, "motion": "推近"}],
        "resolution": "1080x1920",
    }
)

_EDIT_JSON = json.dumps(
    {
        "cut_plan": ["前3秒钩子", "高频转场"],
        "captions": ["关键参数花字"],
        "transitions": "快切",
        "deliverables": ["成片_竖版.mp4"],
        "platform_variants": ["抖音版", "小红书版"],
    }
)

_REVIEW_JSON = json.dumps(
    {
        "period": "日",
        "summary": "首发表现良好",
        "key_metrics": {"play": 12000, "completion_rate": 0.41, "engagement_rate": 0.08},
        "highlights": ["完播率高于均值"],
        "issues": ["转发率偏低"],
        "optimization_suggestions": ["编导：前3秒强化钩子"],
    }
)

_AGENT_OUTPUT = {
    "01-positioning": _POSITIONING_JSON,
    "02-content": _SCRIPT_JSON,
    "03-art": _ART_JSON,
    "04-video": _VIDEO_JSON,
    "05-editing": _EDIT_JSON,
    "06-operation": _REVIEW_JSON,
}


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    """拦截真实网关调用：PIPELINE 各步是真实 LLMAgent，测试不触网。按 agent_code 返回对应形状。"""

    async def fake_chat(self, session, org_id, agent_code, messages):
        content = _AGENT_OUTPUT.get(agent_code, _POSITIONING_JSON)
        return CompletionResult(content, "deepseek-chat", 10, 20, 30), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)
    # 禁用 Ark 真实出片（04 视频 Agent 会据此跳过生成），测试不触网、不计费
    monkeypatch.setattr("app.config.settings.ark_api_key", "")


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
async def test_agent_knowledge_is_scoped_to_client_project_and_active_status(session) -> None:
    org = Org(name="Knowledge scope org")
    current_client = Client(org=org, name="Current client")
    other_client = Client(org=org, name="Other client")
    current_project = Project(org=org, client=current_client, name="Current project")
    sibling_project = Project(org=org, client=current_client, name="Sibling project")
    foreign_project = Project(org=org, client=other_client, name="Foreign project")
    session.add_all(
        [org, current_client, other_client, current_project, sibling_project, foreign_project]
    )
    await session.flush()

    def entry(
        title: str,
        *,
        client: Client,
        project: Project | None,
        status: str = "active",
    ) -> KnowledgeEntry:
        return KnowledgeEntry(
            org_id=org.id,
            client_id=client.id,
            project_id=project.id if project is not None else None,
            category=KnowledgeCategory.USER_PERSONA,
            title=title,
            content=title,
            payload={},
            source_type="manual",
            source_label="test",
            status=status,
        )

    session.add_all(
        [
            entry("client-global", client=current_client, project=None),
            entry("current-project", client=current_client, project=current_project),
            entry("sibling-project", client=current_client, project=sibling_project),
            entry("foreign-client", client=other_client, project=foreign_project),
            entry(
                "archived-current-project",
                client=current_client,
                project=current_project,
                status="archived",
            ),
        ]
    )
    await session.commit()

    knowledge = await OrchestrationEngine()._knowledge(
        session,
        org_id=org.id,
        client_id=current_client.id,
        project_id=current_project.id,
    )

    titles = {
        item["title"]
        for category_items in knowledge.values()
        for item in category_items
    }
    assert titles == {"client-global", "current-project"}


@pytest.mark.asyncio
async def test_pipeline_runs_until_forced_gate(session, content_item) -> None:
    emit = FakeEmit()
    engine = OrchestrationEngine(emit=emit)
    await engine.start(session, content_item.id)
    await session.refresh(content_item)

    # 跑到首个强制门（Gate3 脚本合规）前：定位 + 编导两步完成
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

    # Gate1 定位 + Gate2 选题 自动放行；Gate3 脚本合规 pending；内容 BLOCKED
    gates = (
        await session.scalars(
            select(GateApproval).where(GateApproval.content_item_id == content_item.id)
        )
    ).all()
    assert sorted(g.status.value for g in gates) == ["auto_passed", "auto_passed", "pending"]
    pending = next(g for g in gates if g.status == GateStatus.PENDING)
    assert pending.gate == GateType.SCRIPT_COMPLIANCE
    assert content_item.status == ContentStatus.BLOCKED
    assert "gate.pending" in emit.events
    assert "agent.done" in emit.events
    approval_event = await session.scalar(
        select(Event).where(
            Event.type == "approval.requested",
            Event.content_item_id == content_item.id,
        )
    )
    assert approval_event is not None
    assert approval_event.payload["approval_kind"] == "gate"
    assert approval_event.payload["source_id"] == pending.id


@pytest.mark.asyncio
async def test_approve_forced_gate_completes_pipeline(session, content_item) -> None:
    """审批两道强制门（Gate3 脚本、Gate5 发布前）后，六阶段全部完成并 published。"""
    emit = FakeEmit()
    engine = OrchestrationEngine(emit=emit)
    await engine.start(session, content_item.id)

    # 第 1 道强制门：脚本合规
    gate3 = await _pending_gate(session, content_item.id)
    assert gate3.gate == GateType.SCRIPT_COMPLIANCE
    await engine.approve_gate(session, gate3.id, user_id=1, approved=True)

    # 续跑至第 2 道强制门：发布前审核
    await session.refresh(content_item)
    gate5 = await _pending_gate(session, content_item.id)
    assert gate5 is not None
    assert gate5.gate == GateType.PRE_PUBLISH_REVIEW
    assert content_item.status == ContentStatus.BLOCKED
    await engine.approve_gate(session, gate5.id, user_id=1, approved=True)

    # 六阶段全部 done，内容 published
    await session.refresh(content_item)
    tasks = (
        await session.scalars(select(AgentTask).where(AgentTask.content_item_id == content_item.id))
    ).all()
    assert len(tasks) == 6
    assert all(t.status == AgentTaskStatus.DONE for t in tasks)
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
    assert len(tasks) == 2  # 定位 + 编导
    assert len(gates) == 3  # Gate1 + Gate2 auto_passed + Gate3 pending，无重复


@pytest.mark.asyncio
async def test_rerun_stage_supersedes_old_version(session, content_item):
    """重跑定位阶段：产 version=2，旧版 version=1 置 superseded。"""
    engine = OrchestrationEngine(emit=FakeEmit())
    await engine.start(session, content_item.id)  # 跑到 Gate3，定位已产 v1

    other_stream = [
        Deliverable(
            content_item_id=content_item.id,
            agent_code="00-decision",
            type=DeliverableType.POSITIONING_STRATEGY,
            version=version,
            status=DeliverableStatus.DRAFT,
            payload={"stream": "decision", "version": version},
        )
        for version in (1, 2)
    ]
    session.add_all(other_stream)
    await session.commit()

    await engine.rerun_stage(session, content_item.id, ContentStage.POSITIONING)

    delivs = (
        await session.scalars(
            select(Deliverable)
            .where(
                Deliverable.content_item_id == content_item.id,
                Deliverable.agent_code == "01-positioning",
                Deliverable.type == DeliverableType.POSITIONING_STRATEGY,
            )
            .order_by(Deliverable.version)
        )
    ).all()
    assert [d.version for d in delivs] == [1, 2]
    assert delivs[0].status == DeliverableStatus.SUPERSEDED
    assert delivs[1].status == DeliverableStatus.DRAFT
    assert [row.status for row in other_stream] == [
        DeliverableStatus.DRAFT,
        DeliverableStatus.DRAFT,
    ]


@pytest.mark.asyncio
async def test_rollback_restores_old_version(session, content_item):
    """回滚到 v1：v1 设回 approved，v2 置 superseded。"""
    engine = OrchestrationEngine(emit=FakeEmit())
    await engine.start(session, content_item.id)
    await engine.rerun_stage(session, content_item.id, ContentStage.POSITIONING)

    v1 = await session.scalar(
        select(Deliverable).where(
            Deliverable.content_item_id == content_item.id,
            Deliverable.type == DeliverableType.POSITIONING_STRATEGY,
            Deliverable.version == 1,
        )
    )
    await engine.rollback_deliverable(session, v1.id)

    delivs = (
        await session.scalars(
            select(Deliverable)
            .where(
                Deliverable.content_item_id == content_item.id,
                Deliverable.type == DeliverableType.POSITIONING_STRATEGY,
            )
            .order_by(Deliverable.version)
        )
    ).all()
    assert delivs[0].status == DeliverableStatus.APPROVED  # v1 恢复生效
    assert delivs[1].status == DeliverableStatus.SUPERSEDED  # v2 退场


@pytest.mark.asyncio
async def test_upstream_uses_latest_active_version(session, content_item, monkeypatch):
    """下游只引用最新生效版：定位产出的新人设应出现在编导收到的上游输入里。"""
    captured: dict[str, list] = {}

    async def capturing_chat(self, sess, org_id, agent_code, messages):
        captured[agent_code] = messages
        if agent_code == "01-positioning":
            payload = json.loads(_POSITIONING_JSON)
            payload["account_persona"] = "最新生效人设"
            return CompletionResult(json.dumps(payload), "deepseek-chat", 1, 1, 2), 0.0
        return CompletionResult(_AGENT_OUTPUT[agent_code], "deepseek-chat", 1, 1, 2), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", capturing_chat)
    engine = OrchestrationEngine(emit=FakeEmit())
    await engine.start(session, content_item.id)
    content_msg = next(m["content"] for m in captured["02-content"] if m["role"] == "user")
    assert "最新生效人设" in content_msg


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
    content_user_msg = next(m["content"] for m in captured["02-content"] if m["role"] == "user")
    assert "positioning_strategy" in content_user_msg
    assert "硬核数码测评" in content_user_msg  # 来自定位 Agent 的输出
    # 定位 Agent（首步）无上游
    positioning_user_msg = next(
        m["content"] for m in captured["01-positioning"] if m["role"] == "user"
    )
    assert "无上游输入" in positioning_user_msg
