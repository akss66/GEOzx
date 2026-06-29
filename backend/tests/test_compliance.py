"""合规检测测试：词库规则 + 引擎在 Gate3 阻塞时自动预检落库。"""

import json

import pytest
from sqlalchemy import select

from app.compliance.checker import check_script
from app.llm.adapters import CompletionResult
from app.models import ComplianceCheck, ContentItem, Org, Project
from app.models.enums import ComplianceRisk
from app.orchestrator.engine import OrchestrationEngine


def test_check_script_pass():
    risk, summary, findings = check_script(
        {"title": "新品开箱实测", "hook": "贵的有道理吗", "scenes": ["开箱", "上手"]}
    )
    assert risk == ComplianceRisk.PASS
    assert findings == []


def test_check_script_block_on_absolute_word():
    risk, _summary, findings = check_script(
        {"title": "全网最好的手机", "hook": "第一名", "scenes": ["实测"]}
    )
    assert risk == ComplianceRisk.BLOCK
    assert any(f["word"] == "最好" for f in findings)
    assert any(f["level"] == "block" for f in findings)


def test_check_script_warn_on_risk_word():
    risk, _summary, findings = check_script(
        {"title": "通勤穿搭", "hook": "加我微信领福利", "scenes": ["展示"]}
    )
    assert risk == ComplianceRisk.WARN
    assert any(f["word"] == "微信" for f in findings)


# —— 引擎集成：Gate3 阻塞时自动预检 ——

_SCRIPT_BLOCK = json.dumps(
    {
        "title": "全网最好用的家用投影",
        "hook": "百分之百满意",
        "scenes": ["开箱", "实测", "结论"],
        "duration_seconds": 45,
    }
)

_POSITIONING = json.dumps(
    {
        "account_persona": "数码测评",
        "target_audience": "科技爱好者",
        "differentiation": ["真机长测"],
        "content_pillars": ["新品首发"],
    }
)


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch):
    async def fake_chat(self, session, org_id, agent_code, messages):
        content = _SCRIPT_BLOCK if agent_code == "02-content" else _POSITIONING
        return CompletionResult(content, "deepseek-chat", 1, 1, 2), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)


async def _emit(*a, **k):
    pass


@pytest.mark.asyncio
async def test_engine_runs_compliance_at_script_gate(session):
    org = Org(name="O")
    project = Project(org=org, name="P")
    ci = ContentItem(project=project, title="测试")
    session.add(ci)
    await session.commit()
    await session.refresh(ci)

    engine = OrchestrationEngine(emit=_emit)
    await engine.start(session, ci.id)  # 跑到 Gate3 脚本合规并阻塞

    check = await session.scalar(
        select(ComplianceCheck).where(ComplianceCheck.content_item_id == ci.id)
    )
    assert check is not None
    assert check.risk == ComplianceRisk.BLOCK  # 脚本含"最好/100%"绝对化用语
    assert check.deliverable_id is not None
    assert any(f["level"] == "block" for f in check.findings)
