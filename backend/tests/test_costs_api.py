"""Cost workspace API: scoped business costs and admin-only technical telemetry."""

import json
from decimal import Decimal

import pytest

from app.models import (
    AgentInvocation,
    AgentToolCall,
    BrainTask,
    Client,
    LLMCall,
    OrchestrationPlan,
    Project,
    ProjectMembership,
    TaskBrief,
)
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    BrainTaskType,
    WorkspaceRole,
)


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _cost_task(session, *, org_id: int, project: Project, title: str, cost: str):
    task = BrainTask(
        org_id=org_id,
        title=title,
        type=BrainTaskType.REVIEW_OPTIMIZATION,
        status=BrainTaskStatus.COMPLETED,
        progress=100,
        current_focus="已完成",
        risk_count=0,
    )
    task.brief = TaskBrief(
        goal="复盘优化",
        project_id=project.id,
        project_name=project.name,
        platforms=["douyin"],
        account_ids=[],
        cycle="本月",
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
        estimated_cost=Decimal(cost),
        requires_human_confirmation=True,
    )
    session.add(task)
    await session.flush()
    invocation = AgentInvocation(
        task_id=task.id,
        agent_code=AgentCode.CONTENT_DIRECTOR,
        agent_name="编导文案专家",
        status=AgentInvocationStatus.DONE,
        model="deepseek-chat",
        token_count=2000,
        cost=Decimal("0"),
    )
    session.add(invocation)
    await session.flush()
    session.add(
        LLMCall(
            org_id=org_id,
            task_id=task.id,
            invocation_id=invocation.id,
            agent_code=AgentCode.CONTENT_DIRECTOR.value,
            provider="deepseek",
            model="deepseek-v4-pro",
            prompt_tokens=1000,
            completion_tokens=500,
            total_tokens=1500,
            cost_usd=float(cost),
            latency_ms=900,
            status="ok",
        )
    )
    session.add(
        LLMCall(
            org_id=org_id,
            task_id=task.id,
            invocation_id=invocation.id,
            agent_code=AgentCode.CONTENT_DIRECTOR.value,
            provider="deepseek",
            model="deepseek-v4-pro",
            prompt_tokens=100,
            completion_tokens=0,
            total_tokens=100,
            cost_usd=0,
            latency_ms=200,
            status="error",
            error="fallback attempt",
        )
    )
    session.add(
        AgentToolCall(
            org_id=org_id,
            task_id=task.id,
            invocation_id=invocation.id,
            module="brain",
            agent_code=AgentCode.CONTENT_DIRECTOR.value,
            tool_code="publish_package_prepare",
            tool_name="发布准备",
            status="done",
            permission_mode="confirm",
            cost=Decimal("0.02"),
        )
    )
    return task


async def _main_agent_call(
    session,
    *,
    org_id: int,
    task: BrainTask,
    cost: float,
) -> None:
    session.add(
        LLMCall(
            org_id=org_id,
            task_id=task.id,
            agent_code=AgentCode.DECISION.value,
            provider="deepseek",
            model="deepseek-v4-pro",
            prompt_tokens=800,
            completion_tokens=200,
            total_tokens=1000,
            cost_usd=cost,
            latency_ms=700,
            status="ok",
        )
    )


@pytest.mark.asyncio
async def test_business_costs_include_main_agent_only_calls(
    client, admin, member, session
):
    workspace_client = Client(org_id=admin.org_id, name="主 Agent 客户")
    project = Project(
        org_id=admin.org_id,
        client=workspace_client,
        name="主 Agent 项目",
    )
    session.add_all([workspace_client, project])
    await session.flush()
    session.add(
        ProjectMembership(
            project_id=project.id,
            user_id=member.id,
            role=WorkspaceRole.LEAD,
        )
    )
    task = BrainTask(
        org_id=admin.org_id,
        title="仅主 Agent 回答",
        type=BrainTaskType.REVIEW_OPTIMIZATION,
        status=BrainTaskStatus.COMPLETED,
        progress=100,
        current_focus="已完成",
        risk_count=0,
    )
    task.brief = TaskBrief(
        goal="解释账号数据",
        project_id=project.id,
        project_name=project.name,
        platforms=["douyin"],
        account_ids=[],
        cycle="本周",
        budget=None,
        content_goal="",
        risk_constraints=[],
        expected_outputs=[],
        confirmation_actions=[],
    )
    session.add(task)
    await session.flush()
    await _main_agent_call(
        session,
        org_id=admin.org_id,
        task=task,
        cost=0.00004,
    )
    await _main_agent_call(
        session,
        org_id=admin.org_id,
        task=task,
        cost=0.00004,
    )
    await session.commit()

    token = await _token(client, "user@test.com", "user-pw-123")
    response = await client.get(
        "/costs/overview",
        headers=_auth(token),
        params={
            "client_id": workspace_client.id,
            "project_id": project.id,
            "days": 30,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["actual_cost"] == 0.0001
    assert body["summary"]["task_count"] == 1
    assert body["summary"]["agent_calls"] == 0
    assert body["by_agent"][0]["agent_name"] == "运营大脑"
    assert body["by_agent"][0]["calls"] == 1
    assert body["by_agent"][0]["failed_calls"] == 0


@pytest.mark.asyncio
async def test_business_costs_are_scoped_to_the_selected_client_and_project(
    client, admin, member, session
):
    first_client = Client(org_id=admin.org_id, name="数码客户")
    second_client = Client(org_id=admin.org_id, name="餐饮客户")
    first_project = Project(
        org_id=admin.org_id,
        client=first_client,
        name="数码增长",
        monthly_cost_budget_usd=Decimal("10.00"),
    )
    second_project = Project(
        org_id=admin.org_id,
        client=second_client,
        name="餐饮增长",
        monthly_cost_budget_usd=Decimal("20.00"),
    )
    session.add_all([first_client, second_client, first_project, second_project])
    await session.flush()
    session.add(
        ProjectMembership(
            project_id=first_project.id,
            user_id=member.id,
            role=WorkspaceRole.LEAD,
        )
    )
    first_task = await _cost_task(
        session,
        org_id=admin.org_id,
        project=first_project,
        title="数码复盘任务",
        cost="0.08",
    )
    await _main_agent_call(
        session,
        org_id=admin.org_id,
        task=first_task,
        cost=0.03,
    )
    await _cost_task(
        session,
        org_id=admin.org_id,
        project=second_project,
        title="餐饮复盘任务",
        cost="0.40",
    )
    await session.commit()

    token = await _token(client, "user@test.com", "user-pw-123")
    response = await client.get(
        "/costs/overview",
        headers=_auth(token),
        params={
            "client_id": first_client.id,
            "project_id": first_project.id,
            "days": 30,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scope"]["client_name"] == "数码客户"
    assert body["scope"]["project_name"] == "数码增长"
    assert body["summary"] == {
        "actual_cost": 0.13,
        "budget": 10.0,
        "budget_usage": 1.3,
        "remaining_budget": 9.87,
        "task_count": 1,
        "agent_calls": 1,
        "tool_calls": 1,
        "failed_operations": 0,
        "budget_status": "healthy",
    }
    assert [row["title"] for row in body["by_task"]] == ["数码复盘任务"]
    assert {row["agent_name"] for row in body["by_agent"]} == {
        "编导文案专家",
        "运营大脑",
    }
    assert {
        row["agent_name"]: (row["calls"], row["failed_calls"])
        for row in body["by_agent"]
    } == {
        "编导文案专家": (1, 0),
        "运营大脑": (1, 0),
    }
    assert body["by_tool"][0]["tool_name"] == "发布准备"
    serialized = json.dumps(body, ensure_ascii=False)
    assert "餐饮" not in serialized
    assert "deepseek" not in serialized
    assert "provider" not in serialized
    assert "tokens" not in serialized

    forbidden = await client.get(
        "/costs/overview",
        headers=_auth(token),
        params={"client_id": second_client.id, "project_id": second_project.id},
    )
    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_technical_costs_are_admin_only_and_include_failures(
    client, admin, member, session
):
    session.add_all(
        [
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
                agent_code=AgentCode.DECISION.value,
                provider="deepseek",
                model="deepseek-chat",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                cost_usd=0,
                latency_ms=400,
                status="error",
                error="temporary upstream error",
            ),
        ]
    )
    await session.commit()
    member_token = await _token(client, "user@test.com", "user-pw-123")
    admin_token = await _token(client, "admin@test.com", "admin-pw-123")

    denied = await client.get("/costs/technical", headers=_auth(member_token))
    assert denied.status_code == 403

    response = await client.get(
        "/costs/technical",
        headers=_auth(admin_token),
        params={"days": 30},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "total_cost": 0.08,
        "total_calls": 2,
        "total_tokens": 1000,
        "failed_calls": 1,
        "fallback_attempts": 1,
        "average_latency_ms": 800,
    }
    assert body["by_provider"][0]["provider"] == "deepseek"
    assert {row["model"] for row in body["by_model"]} == {
        "deepseek-chat",
        "deepseek-reasoner",
    }
    assert sum(row["failed_calls"] for row in body["by_model"]) == 1
