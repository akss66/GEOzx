"""E10 闭环反馈：运营复盘建议落库、广播，并可追踪采纳/验证状态。"""

import json

import pytest
from sqlalchemy import select

from app.llm.adapters import CompletionResult
from app.models import Account, ContentItem, GateApproval, OptimizationSuggestion, Org, Project
from app.models.enums import ContentStage, GateStatus, OptimizationSuggestionStatus, Platform
from app.orchestrator.engine import OrchestrationEngine


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


_PAYLOADS = {
    "01-positioning": {
        "account_persona": "专业测评号",
        "target_audience": "25-35 岁科技爱好者",
        "differentiation": ["真机长测"],
        "content_pillars": ["新品", "横评"],
    },
    "02-content": {
        "title": "三分钟看懂新品",
        "hook": "这台机器贵得有道理吗？",
        "scenes": ["开箱", "实测", "结论"],
        "duration_seconds": 45,
    },
    "03-art": {
        "visual_style": "冷调科技风",
        "prompts": ["产品特写", "参数对比画面"],
        "aspect_ratio": "9:16",
    },
    "04-video": {
        "tool": "seedance",
        "clips": [{"prompt": "产品特写", "duration_seconds": 5}],
        "resolution": "1080x1920",
    },
    "05-editing": {
        "cut_plan": ["前3秒钩子"],
        "captions": ["关键参数花字"],
        "transitions": "快切",
        "deliverables": ["final.mp4"],
        "platform_variants": ["抖音版"],
    },
    "06-operation": {
        "period": "日",
        "summary": "完播不错，但互动偏弱",
        "key_metrics": {"play": 12000, "completion_rate": 0.41},
        "highlights": ["完播率高于均值"],
        "issues": ["评论率偏低"],
        "optimization_suggestions": [
            "编导：前3秒增加反差问题",
            "美术：首帧强化产品利益点",
        ],
    },
}


@pytest.fixture(autouse=True)
def _stub_external_calls(monkeypatch):
    async def fake_chat(self, session, org_id, agent_code, messages):
        return (
            CompletionResult(
                json.dumps(_PAYLOADS[agent_code], ensure_ascii=False),
                "deepseek-chat",
                1,
                1,
                2,
            ),
            0.0,
        )

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)
    monkeypatch.setattr("app.config.settings.ark_api_key", "")


class FakeEmit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict | None]] = []

    async def __call__(
        self, event_type, payload=None, content_item_id=None, project_id=None
    ) -> None:
        self.events.append((event_type, payload))


@pytest.mark.asyncio
async def test_operation_review_creates_suggestions_and_broadcasts(session):
    org = Org(name="O")
    project = Project(org=org, name="P")
    ci = ContentItem(project=project, title="测试内容")
    session.add(ci)
    await session.commit()
    await session.refresh(ci)

    emit = FakeEmit()
    engine = OrchestrationEngine(emit=emit)
    await engine.start(session, ci.id)

    while True:
        gate = await session.scalar(
            select(GateApproval).where(
                GateApproval.content_item_id == ci.id,
                GateApproval.status == GateStatus.PENDING,
            )
        )
        if gate is None:
            break
        await engine.approve_gate(
            session,
            gate.id,
            user_id=1,
            approved=True,
            comment=f"approve {gate.gate.value}",
        )

    rows = (
        await session.scalars(
            select(OptimizationSuggestion).where(
                OptimizationSuggestion.content_item_id == ci.id
            )
        )
    ).all()
    assert [r.suggestion for r in rows] == _PAYLOADS["06-operation"][
        "optimization_suggestions"
    ]
    assert {r.status for r in rows} == {OptimizationSuggestionStatus.SUGGESTED}
    assert [event for event, _payload in emit.events].count("optimization.suggestion") == 2


@pytest.mark.asyncio
async def test_suggestion_api_lists_accepts_and_verifies(client, admin, session):
    project = Project(org_id=admin.org_id, name="P")
    session.add(project)
    await session.flush()
    ci = ContentItem(project_id=project.id, title="内容A")
    session.add(ci)
    await session.flush()
    suggestion = OptimizationSuggestion(
        org_id=admin.org_id,
        content_item_id=ci.id,
        source_deliverable_id=None,
        target_stage="content_direction",
        suggestion="编导：前3秒增加反差问题",
    )
    session.add(suggestion)
    await session.commit()
    await session.refresh(suggestion)

    token = await _token(client, "admin@test.com", "admin-pw-123")
    listing = await client.get("/optimization-suggestions", headers=_auth(token))
    assert listing.status_code == 200
    body = listing.json()
    assert body[0]["suggestion"] == "编导：前3秒增加反差问题"
    assert body[0]["status"] == "suggested"
    assert body[0]["content_title"] == "内容A"

    accepted = await client.patch(
        f"/optimization-suggestions/{suggestion.id}",
        headers=_auth(token),
        json={"status": "accepted", "note": "下周期采用"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    verified = await client.patch(
        f"/optimization-suggestions/{suggestion.id}",
        headers=_auth(token),
        json={"status": "verified", "note": "完播率提升 5%"},
    )
    assert verified.status_code == 200
    assert verified.json()["status"] == "verified"


@pytest.mark.asyncio
async def test_suggestion_can_be_sent_to_brain_as_next_cycle_brief(client, admin, session):
    project = Project(org_id=admin.org_id, name="复盘项目")
    session.add(project)
    await session.flush()
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="复盘账号",
        auth={"auth_status": "manual"},
    )
    session.add(account)
    await session.flush()
    ci = ContentItem(project_id=project.id, account_id=account.id, title="内容A")
    session.add(ci)
    await session.flush()
    suggestion = OptimizationSuggestion(
        org_id=admin.org_id,
        content_item_id=ci.id,
        source_deliverable_id=None,
        target_stage="content_direction",
        suggestion="编导：前3秒增加反差问题",
    )
    session.add(suggestion)
    await session.commit()
    await session.refresh(suggestion)

    token = await _token(client, "admin@test.com", "admin-pw-123")
    created = await client.post(
        f"/optimization-suggestions/{suggestion.id}/send-to-brain",
        headers=_auth(token),
    )

    assert created.status_code == 201
    task = created.json()
    assert task["status"] == "pending_confirmation"
    assert task["type"] == "review_optimization"
    assert task["brief"]["project_id"] == project.id
    assert "内容A" in task["brief"]["goal"]
    assert "编导：前3秒增加反差问题" in task["brief"]["goal"]

    await session.refresh(suggestion)
    assert suggestion.status == OptimizationSuggestionStatus.ACCEPTED
    assert suggestion.accepted_at is not None


@pytest.mark.asyncio
async def test_accepted_suggestions_are_injected_into_next_cycle(session, monkeypatch):
    captured: dict[str, list] = {}

    async def capturing_chat(self, sess, org_id, agent_code, messages):
        captured[agent_code] = messages
        return (
            CompletionResult(
                json.dumps(_PAYLOADS[agent_code], ensure_ascii=False),
                "deepseek-chat",
                1,
                1,
                2,
            ),
            0.0,
        )

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", capturing_chat)

    org = Org(name="O")
    project = Project(org=org, name="P")
    previous = ContentItem(project=project, title="上一条内容")
    current = ContentItem(project=project, title="下一条内容")
    session.add_all([previous, current])
    await session.flush()
    session.add(
        OptimizationSuggestion(
            org_id=org.id,
            content_item_id=previous.id,
            source_deliverable_id=None,
            target_stage=ContentStage.CONTENT_DIRECTION.value,
            suggestion="编导：前3秒增加反差问题",
            status=OptimizationSuggestionStatus.ACCEPTED,
        )
    )
    await session.commit()
    await session.refresh(current)

    engine = OrchestrationEngine(emit=FakeEmit())
    await engine.start(session, current.id)

    content_user_msg = next(
        m["content"] for m in captured["02-content"] if m["role"] == "user"
    )
    assert "已采纳优化建议" in content_user_msg
    assert "编导：前3秒增加反差问题" in content_user_msg
