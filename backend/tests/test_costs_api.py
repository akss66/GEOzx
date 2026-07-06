"""成本看板 API：按模型、子 Agent、运营大脑任务聚合成本。"""

from decimal import Decimal

import pytest

from app.models import AgentInvocation, BrainTask, LLMCall, OrchestrationPlan, TaskBrief
from app.models.enums import AgentCode, AgentInvocationStatus, BrainTaskStatus, BrainTaskType


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_cost_overview_groups_model_agent_and_task_costs(client, admin, session):
    task = BrainTask(
        org_id=admin.org_id,
        title="复盘优化任务",
        type=BrainTaskType.REVIEW_OPTIMIZATION,
        status=BrainTaskStatus.COMPLETED,
        progress=100,
        current_focus="已完成",
        risk_count=0,
    )
    task.brief = TaskBrief(
        goal="复盘优化",
        platforms=["douyin"],
        account_ids=[],
        cycle="本周",
        budget=None,
        content_goal="优化完播",
        risk_constraints=[],
        expected_outputs=[],
        confirmation_actions=[],
    )
    task.plan = OrchestrationPlan(
        summary="计划",
        steps=[],
        quality_gates=[],
        estimated_cost=Decimal("0.20"),
        requires_human_confirmation=True,
    )
    session.add(task)
    await session.flush()
    session.add_all(
        [
            AgentInvocation(
                task_id=task.id,
                agent_code=AgentCode.DECISION,
                agent_name="运营大脑",
                status=AgentInvocationStatus.DONE,
                model="deepseek-reasoner",
                token_count=1000,
                cost=Decimal("0.08"),
            ),
            AgentInvocation(
                task_id=task.id,
                agent_code=AgentCode.CONTENT_DIRECTOR,
                agent_name="编导文案专家",
                status=AgentInvocationStatus.DONE,
                model="deepseek-chat",
                token_count=2000,
                cost=Decimal("0.12"),
            ),
            LLMCall(
                org_id=admin.org_id,
                agent_code=AgentCode.DECISION.value,
                provider="deepseek",
                model="deepseek-reasoner",
                prompt_tokens=600,
                completion_tokens=400,
                total_tokens=1000,
                cost_usd=0.08,
                latency_ms=1200,
                status="ok",
            ),
            LLMCall(
                org_id=admin.org_id,
                agent_code=AgentCode.CONTENT_DIRECTOR.value,
                provider="deepseek",
                model="deepseek-chat",
                prompt_tokens=1300,
                completion_tokens=700,
                total_tokens=2000,
                cost_usd=0.12,
                latency_ms=800,
                status="ok",
            ),
        ]
    )
    await session.commit()

    token = await _token(client, "admin@test.com", "admin-pw-123")
    resp = await client.get("/costs/overview", headers=_auth(token))

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_cost"] == 0.2
    assert body["total_calls"] == 2
    assert body["total_tokens"] == 3000
    assert body["by_brain"][0]["type"] == "review_optimization"
    assert body["by_brain"][0]["tasks"] == 1
    assert body["by_brain"][0]["cost"] == 0.2
    assert {row["model"]: row["cost"] for row in body["by_model"]} == {
        "deepseek-chat": 0.12,
        "deepseek-reasoner": 0.08,
    }
    assert body["by_agent"][0]["agent_name"] == "编导文案专家"
    assert body["by_agent"][0]["cost"] == 0.12
    assert body["by_task"][0]["title"] == "复盘优化任务"
    assert body["by_task"][0]["type"] == "review_optimization"
    assert body["by_task"][0]["cost"] == 0.2
