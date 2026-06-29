"""编排引擎测试：流转 / 版本化 / 质量门阻塞与审批（fake emit，SQLite）。"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models import AgentTask, ContentItem, Deliverable, GateApproval, Org, Project
from app.models.enums import (
    AgentTaskStatus,
    ContentStage,
    ContentStatus,
    DeliverableType,
    GateStatus,
)
from app.orchestrator.engine import OrchestrationEngine


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
