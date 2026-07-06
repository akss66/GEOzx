import pytest

from app.models import AgentToolCall, BrainTask
from app.models.enums import BrainTaskStatus


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_and_get_agents(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    listing = await client.get("/agents", headers=headers)
    assert listing.status_code == 200
    agents = listing.json()
    assert len(agents) == 9
    assert agents[0]["code"] == "00-decision"

    detail = await client.get("/agents/02-content-director", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == "编导文案专家"


@pytest.mark.asyncio
async def test_agent_profile_includes_tool_call_summary(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    task = BrainTask(
        org_id=admin.org_id,
        title="agent tool summary task",
        status=BrainTaskStatus.PENDING_ACCEPTANCE,
        current_focus="waiting for tool approval",
    )
    session.add(task)
    await session.flush()
    session.add(
        AgentToolCall(
            org_id=admin.org_id,
            task_id=task.id,
            module="brain",
            agent_code="02-content-director",
            tool_code="brief_builder",
            tool_name="Brief Builder",
            status="waiting_approval",
            permission_mode="confirm",
            requires_human_confirmation=True,
            input_summary="account context and goal",
            output_summary="brief generated",
            cost=0,
            meta={"agent_name": "content director"},
        )
    )
    await session.commit()

    detail = await client.get("/agents/02-content-director", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["tool_summary"]["total_calls"] == 1
    assert body["tool_summary"]["pending_approvals"] == 1
    assert body["tool_summary"]["failed_calls"] == 0
    assert body["tool_summary"]["recent_calls"][0]["tool_code"] == "brief_builder"
    assert body["tool_summary"]["recent_calls"][0]["status"] == "waiting_approval"


@pytest.mark.asyncio
async def test_admin_can_update_agent_config_but_member_cannot(client, admin, member):
    admin_token = await _token(client, "admin@test.com", "admin-pw-123")
    member_token = await _token(client, "user@test.com", "user-pw-123")

    denied = await client.patch(
        "/agents/02-content-director/config",
        headers=_auth(member_token),
        json={"primary_model": "deepseek-reasoner"},
    )
    assert denied.status_code == 403

    updated = await client.patch(
        "/agents/02-content-director/config",
        headers=_auth(admin_token),
        json={
            "primary_model": "deepseek-reasoner",
            "fallback_model": "deepseek-chat",
            "automation_level": "confirm",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["model"] == "deepseek-reasoner"
    assert updated.json()["fallback_model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_direct_agent_invoke_flows_back_to_brain(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    invoked = await client.post(
        "/agents/06-operator/invoke",
        headers=headers,
        json={"prompt": "总结昨天抖音评论里的用户问题"},
    )
    assert invoked.status_code == 200
    body = invoked.json()
    assert body["message"].endswith("运营大脑。")
    assert body["invocation"]["agent_code"] == "06-operator"
    assert body["invocation"]["status"] == "done"

    tasks = await client.get("/brain/tasks", headers=headers)
    assert tasks.status_code == 200
    assert tasks.json()[0]["title"].startswith("直接调用")
