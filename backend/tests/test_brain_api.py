import json

import pytest
from sqlalchemy import select

from app.llm.adapters import CompletionResult
from app.models import ContentItem, GateApproval
from app.models.enums import GateStatus, GateType

_POSITIONING_JSON = json.dumps(
    {
        "account_persona": "露营装备测评号",
        "target_audience": "25-35 岁户外用户",
        "differentiation": ["夜间真实测试", "装备清单拆解"],
        "content_pillars": ["新品冷启动", "场景测评"],
    }
)

_SCRIPT_JSON = json.dumps(
    {
        "title": "这盏营地灯真的能救场吗",
        "hook": "停电以后，最先慌的不是人，是没电的灯。",
        "scenes": ["夜间开场", "亮度测试", "收纳对比"],
        "duration_seconds": 45,
        "bgm_suggestion": "轻快户外氛围",
    }
)


@pytest.fixture(autouse=True)
def _stub_pipeline_llm(monkeypatch):
    async def fake_chat(self, session, org_id, agent_code, messages):
        content = _POSITIONING_JSON if agent_code == "01-positioning" else _SCRIPT_JSON
        return CompletionResult(content, "deepseek-chat", 10, 20, 30), 0.0

    monkeypatch.setattr("app.llm.gateway.LLMGateway.chat", fake_chat)
    monkeypatch.setattr("app.config.settings.ark_api_key", "")


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _authorized_douyin_account(client, headers: dict[str, str], name: str = "抖音账号") -> int:
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": name, "platform": "douyin", "external_account_id": "open-id"},
        )
    ).json()
    await client.patch(
        f"/accounts/{account['id']}/integration",
        headers=headers,
        json={
            "integration_status": "connected",
            "auth_status": "authorized",
            "data_sync_status": "pending",
        },
    )
    return account["id"]


@pytest.mark.asyncio
async def test_brain_task_lifecycle(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "为 7 月新品做一组抖音冷启动内容",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    assert draft.status_code == 201
    task = draft.json()
    assert task["status"] == "pending_confirmation"
    assert task["brief"]["goal"].startswith("为 7 月新品")
    assert task["plan"]["steps"][0]["agent_code"] == "01-positioning"

    listing = await client.get("/brain/tasks", headers=headers)
    assert listing.status_code == 200
    assert listing.json()[0]["id"] == task["id"]

    confirmed = await client.post(f"/brain/tasks/{task['id']}/confirm", headers=headers)
    assert confirmed.status_code == 200
    confirmed_body = confirmed.json()
    assert confirmed_body["status"] == "pending_acceptance"
    assert confirmed_body["content_item_id"] is not None
    assert "质量门" in confirmed_body["current_focus"]

    invocations = await client.get(f"/brain/tasks/{task['id']}/invocations", headers=headers)
    assert invocations.status_code == 200
    invocation_rows = invocations.json()
    assert {row["agent_code"] for row in invocation_rows} == {
        "01-positioning",
        "02-content-director",
    }
    assert all(row["status"] == "done" for row in invocation_rows)

    tool_calls = await client.get(f"/brain/tasks/{task['id']}/tool-calls", headers=headers)
    assert tool_calls.status_code == 200
    tool_rows = tool_calls.json()
    assert {row["tool_code"] for row in tool_rows} >= {
        "account_context",
        "profile_snapshot",
        "brief_builder",
        "compliance_precheck",
    }
    assert any(row["permission_mode"] == "auto" for row in tool_rows)
    assert any(row["requires_human_confirmation"] is True for row in tool_rows)

    pending_tool_approvals = await client.get(
        "/brain/tool-calls/pending-approvals", headers=headers
    )
    assert pending_tool_approvals.status_code == 200
    pending_tool_rows = pending_tool_approvals.json()
    assert any(row["task_id"] == task["id"] for row in pending_tool_rows)
    approval_id = next(
        row["id"] for row in pending_tool_rows if row["tool_code"] == "brief_builder"
    )

    approved_tool = await client.post(
        f"/brain/tool-calls/{approval_id}/approve",
        headers=headers,
        json={"approved": True, "comment": "确认通过"},
    )
    assert approved_tool.status_code == 200
    assert approved_tool.json()["status"] == "success"
    assert approved_tool.json()["meta"]["decision"]["approved"] is True

    after_tool_approvals = await client.get(
        "/brain/tool-calls/pending-approvals", headers=headers
    )
    assert all(row["id"] != approval_id for row in after_tool_approvals.json())

    acceptances = await client.get(f"/brain/tasks/{task['id']}/acceptances", headers=headers)
    assert acceptances.status_code == 200
    acceptance_rows = acceptances.json()
    assert {row["deliverable_type"] for row in acceptance_rows} == {
        "positioning_strategy",
        "video_script",
    }
    acceptance_id = next(
        row["id"] for row in acceptance_rows if row["deliverable_type"] == "video_script"
    )

    rejudge = await client.post(
        f"/brain/tasks/{task['id']}/rejudge",
        headers=headers,
        json={"acceptance_id": acceptance_id},
    )
    assert rejudge.status_code == 200
    assert rejudge.json()["status"] == "rerun_requested"
    assert rejudge.json()["brain_rejudge_summary"]

    for row in acceptance_rows:
        accepted = await client.post(
            f"/brain/tasks/{task['id']}/accept",
            headers=headers,
            json={"acceptance_id": row["id"], "reviewer_note": "验收通过"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "approved"

    closed = await client.post(f"/brain/tasks/{task['id']}/close-memory", headers=headers)
    assert closed.status_code == 200
    assert closed.json()["closed"] is True


@pytest.mark.asyncio
async def test_brain_confirm_creates_content_item_and_pending_gate(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "为露营新品生成一轮短视频脚本",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    task_id = draft.json()["id"]

    confirmed = await client.post(f"/brain/tasks/{task_id}/confirm", headers=headers)
    assert confirmed.status_code == 200
    content_item_id = confirmed.json()["content_item_id"]

    content_item = await session.get(ContentItem, content_item_id)
    assert content_item is not None
    assert content_item.title.startswith("为露营新品")
    assert content_item.account_id == account_id

    pending_gate = await session.scalar(
        select(GateApproval).where(
            GateApproval.content_item_id == content_item_id,
            GateApproval.status == GateStatus.PENDING,
        )
    )
    assert pending_gate is not None
    assert pending_gate.gate == GateType.SCRIPT_COMPLIANCE


@pytest.mark.asyncio
async def test_brain_draft_binds_project_account_group_platforms_and_accounts(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    project_id = (
        await client.post("/projects", headers=headers, json={"name": "露营项目"})
    ).json()["id"]
    group_id = (
        await client.post(
            "/account-groups",
            headers=headers,
            json={"name": "露营账号组", "dimension": "track"},
        )
    ).json()["id"]
    douyin_account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "露营一号", "platform": "douyin", "group_id": group_id},
        )
    ).json()["id"]
    await client.patch(
        f"/accounts/{douyin_account_id}/integration",
        headers=headers,
        json={
            "integration_status": "connected",
            "auth_status": "authorized",
            "data_sync_status": "pending",
        },
    )
    xhs_account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "小红书一号", "platform": "xiaohongshu", "group_id": group_id},
        )
    ).json()["id"]

    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "为露营项目生成矩阵分发计划",
            "project_id": project_id,
            "account_group_id": group_id,
            "platforms": ["douyin"],
            "account_ids": [douyin_account_id],
        },
    )

    assert draft.status_code == 201
    brief = draft.json()["brief"]
    assert brief["project_id"] == project_id
    assert brief["project_name"] == "露营项目"
    assert brief["account_group_id"] == group_id
    assert brief["account_group_name"] == "露营账号组"
    assert brief["platforms"] == ["douyin"]
    assert brief["account_ids"] == [douyin_account_id]

    mismatch = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "平台不匹配应拒绝",
            "account_group_id": group_id,
            "platforms": ["douyin"],
            "account_ids": [xhs_account_id],
        },
    )
    assert mismatch.status_code == 400


@pytest.mark.asyncio
async def test_brain_draft_plan_marks_parallel_and_skipped_steps(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)

    content_task = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "为 7 月新品做一组短视频内容",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    assert content_task.status_code == 201
    content_steps = {
        step["id"]: step for step in content_task.json()["plan"]["steps"]
    }
    assert list(content_steps) == [
        "step-positioning",
        "step-script",
        "step-art",
        "step-video",
        "step-editing",
        "step-operation",
    ]
    assert content_steps["step-script"]["depends_on"] == ["step-positioning"]
    assert content_steps["step-art"]["depends_on"] == ["step-positioning"]
    assert content_steps["step-video"]["depends_on"] == ["step-script", "step-art"]
    assert "publish_package_prepare" in content_steps["step-operation"]["tool_codes"]
    assert all(step["status"] == "planned" for step in content_steps.values())

    diagnosis_task = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "诊断账号完播率下降原因",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    assert diagnosis_task.status_code == 201
    diagnosis_steps = {
        step["id"]: step for step in diagnosis_task.json()["plan"]["steps"]
    }
    assert diagnosis_steps["step-art"]["status"] == "skipped"
    assert diagnosis_steps["step-video"]["status"] == "skipped"
    assert diagnosis_steps["step-editing"]["status"] == "skipped"
    assert diagnosis_steps["step-operation"]["depends_on"] == ["step-script"]


@pytest.mark.asyncio
async def test_rerun_requires_reason(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _authorized_douyin_account(client, headers)
    draft = await client.post(
        "/brain/tasks/draft",
        headers=headers,
        json={
            "goal": "诊断账号完播率下降原因",
            "platforms": ["douyin"],
            "account_ids": [account_id],
        },
    )
    task_id = draft.json()["id"]
    await client.post(f"/brain/tasks/{task_id}/confirm", headers=headers)
    acceptance = (
        await client.get(f"/brain/tasks/{task_id}/acceptances", headers=headers)
    ).json()[0]

    resp = await client.post(
        f"/brain/tasks/{task_id}/rerun",
        headers=headers,
        json={"acceptance_id": acceptance["id"], "reason": "", "rerun_scope": "downstream"},
    )
    assert resp.status_code == 422
