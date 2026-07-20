import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AgentToolCall,
    BrainTask,
    Client,
    ComplianceCheck,
    ContentItem,
    Deliverable,
    DeliverableAcceptance,
    Event,
    GateApproval,
    Project,
    ProjectAccount,
    ProjectMembership,
    TaskBrief,
)
from app.models.enums import (
    AgentCode,
    ComplianceRisk,
    DeliverableAcceptanceStatus,
    DeliverableStatus,
    DeliverableType,
    GateStatus,
    GateType,
    Platform,
    WorkspaceRole,
)


async def _token(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _approval_data(admin, member, session):
    client = Client(org_id=admin.org_id, name="审批客户")
    session.add(client)
    await session.flush()
    visible_project = Project(org_id=admin.org_id, client_id=client.id, name="可审批项目")
    hidden_project = Project(org_id=admin.org_id, client_id=client.id, name="隐藏项目")
    session.add_all([visible_project, hidden_project])
    await session.flush()
    account = Account(
        org_id=admin.org_id,
        client_id=client.id,
        project_id=visible_project.id,
        platform=Platform.DOUYIN,
        nickname="审批账号",
    )
    visible_content = ContentItem(
        project_id=visible_project.id,
        title="真实脚本审批",
    )
    hidden_content = ContentItem(project_id=hidden_project.id, title="不可见审批")
    session.add_all([account, visible_content, hidden_content])
    await session.flush()
    visible_content.account_id = account.id
    script = Deliverable(
        content_item_id=visible_content.id,
        agent_code="02-content",
        type=DeliverableType.VIDEO_SCRIPT,
        version=2,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={
            "title": "实测脚本",
            "hook": "前三秒直接给结论",
            "scenes": ["冲突开场", "参数实测", "结论"],
            "duration_seconds": 42,
            "bgm_suggestion": "克制电子节奏",
        },
    )
    session.add(script)
    await session.flush()
    gate = GateApproval(
        content_item_id=visible_content.id,
        gate=GateType.SCRIPT_COMPLIANCE,
        status=GateStatus.PENDING,
    )
    hidden_gate = GateApproval(
        content_item_id=hidden_content.id,
        gate=GateType.PRE_PUBLISH_REVIEW,
        status=GateStatus.PENDING,
    )
    check = ComplianceCheck(
        content_item_id=visible_content.id,
        deliverable_id=script.id,
        risk=ComplianceRisk.WARN,
        summary="包含一处需要人工确认的绝对化表达",
        findings=[{"word": "最好", "category": "绝对化", "level": "warn"}],
    )
    task = BrainTask(
        org_id=admin.org_id,
        content_item_id=visible_content.id,
        title="发布准备",
    )
    task.brief = TaskBrief(
        goal="准备抖音发布包",
        project_id=visible_project.id,
        project_name=visible_project.name,
        platforms=["douyin"],
        account_ids=[account.id],
    )
    tool = AgentToolCall(
        org_id=admin.org_id,
        task=task,
        module="content_production",
        agent_code="06-operation",
        tool_code="publish_package_prepare",
        tool_name="发布包准备",
        status="waiting_approval",
        permission_mode="confirm",
        requires_human_confirmation=True,
        input_summary="整理抖音发布包",
        output_summary="发布包已准备，等待人工确认",
        meta={
            "content_item_id": visible_content.id,
            "content_title": visible_content.title,
            "platform": "douyin",
            "risk": "warn",
            "findings": [{"level": "warn", "code": "title.long", "message": "标题偏长"}],
            "publish_package": {
                "platform": "douyin",
                "account_id": account.id,
                "content_type": "video",
                "title": "真实发布标题",
                "body": "真实发布正文",
                "topics": ["数码实测"],
                "scheduled_at": None,
                "material_ids": [7],
                "cover_material_id": 8,
                "visibility": "public",
                "allow_comment": True,
                "execution_mode": "manual_checklist",
                "manual_steps": ["核对账号", "上传素材"],
            },
        },
    )
    acceptance = DeliverableAcceptance(
        task=task,
        deliverable_id=script.id,
        agent_code=AgentCode.CONTENT_DIRECTOR,
        agent_name="编导文案专家",
        deliverable_type=DeliverableType.VIDEO_SCRIPT,
        title="视频脚本验收",
        version=2,
        summary="脚本已完成，等待采用或重做。",
        status=DeliverableAcceptanceStatus.PENDING,
        acceptance_items=[{"label": "前三秒钩子", "status": "pass", "note": "结论前置"}],
    )
    membership = ProjectMembership(
        project_id=visible_project.id,
        user_id=member.id,
        role=WorkspaceRole.REVIEWER,
    )
    session.add_all([gate, hidden_gate, check, task, tool, acceptance, membership])
    await session.commit()
    return client, visible_project, account, gate, tool, acceptance, membership


@pytest.mark.asyncio
async def test_selected_scope_hides_task_runtime_and_approval_entries(client, admin, member, session):
    _, _, _, _, tool, acceptance, _ = await _approval_data(admin, member, session)
    member.account_scope_mode = "selected"
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")
    headers = _auth(token)

    pending = await client.get("/brain/tool-calls/pending-approvals", headers=headers)
    runtime = await client.get(f"/brain/tasks/{tool.task_id}/runtime", headers=headers)
    acceptances = await client.get(f"/brain/tasks/{tool.task_id}/acceptances", headers=headers)
    close_memory = await client.post(
        f"/brain/tasks/{tool.task_id}/close-memory", headers=headers
    )
    approval = await client.post(
        f"/brain/tool-calls/{tool.id}/approve",
        headers=headers,
        json={"approved": True, "comment": "Hidden account must not be decidable"},
    )
    acceptance_response = await client.post(
        f"/brain/tasks/{tool.task_id}/accept",
        headers=headers,
        json={"acceptance_id": acceptance.id, "reviewer_note": "Hidden account"},
    )

    assert pending.status_code == 200
    assert pending.json() == []
    assert runtime.status_code == 404
    assert acceptances.status_code == 404
    assert close_memory.status_code == 404
    assert approval.status_code == 404
    assert acceptance_response.status_code == 404


@pytest.mark.asyncio
async def test_approval_workspace_aggregates_real_scoped_items(client, admin, member, session):
    workspace_client, project, account, gate, tool, _, _ = await _approval_data(
        admin, member, session
    )
    token = await _token(client, "user@test.com", "user-pw-123")

    response = await client.get(
        "/approvals/workspace",
        headers=_auth(token),
        params={
            "client_id": workspace_client.id,
            "project_id": project.id,
            "account_id": account.id,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["can_decide"] is True
    assert body["counts"] == {"total": 3, "critical": 0, "high": 2, "medium": 1}
    assert [item["kind"] for item in body["items"]] == [
        "gate",
        "tool_call",
        "deliverable",
    ]
    assert {item["project_id"] for item in body["items"]} == {project.id}
    assert {item["account_name"] for item in body["items"]} == {"审批账号"}
    gate_item = next(item for item in body["items"] if item["source_id"] == gate.id)
    assert gate_item["risk_level"] == "high"
    assert gate_item["preview"]["deliverable"]["payload"]["hook"] == "前三秒直接给结论"
    tool_item = next(
        item
        for item in body["items"]
        if item["kind"] == "tool_call" and item["source_id"] == tool.id
    )
    assert tool_item["preview"]["publish_package"]["title"] == "真实发布标题"
    assert tool_item["impact"]


@pytest.mark.asyncio
async def test_only_reviewer_or_lead_can_decide_project_approval(client, admin, member, session):
    _, _, _, gate, tool, acceptance, membership = await _approval_data(admin, member, session)
    token = await _token(client, "user@test.com", "user-pw-123")
    membership.role = WorkspaceRole.EDITOR
    await session.commit()

    gate_denied = await client.post(
        f"/gates/{gate.id}/approve",
        headers=_auth(token),
        json={"approved": True, "comment": "编辑无权审批"},
    )
    tool_denied = await client.post(
        f"/brain/tool-calls/{tool.id}/approve",
        headers=_auth(token),
        json={"approved": True, "comment": "编辑无权审批"},
    )

    assert gate_denied.status_code == 403
    assert tool_denied.status_code == 403

    membership.role = WorkspaceRole.REVIEWER
    await session.commit()
    gate_approved = await client.post(
        f"/gates/{gate.id}/approve",
        headers=_auth(token),
        json={"approved": False, "comment": "审核员要求修改"},
    )
    assert gate_approved.status_code == 200
    audit = await session.scalar(
        select(Event).where(
            Event.type == "approval.decided",
            Event.content_item_id == gate.content_item_id,
        )
    )
    assert audit is not None
    assert audit.payload["approval_kind"] == "gate"
    assert audit.payload["approved"] is False

    admin_token = await _token(client, "admin@test.com", "admin-pw-123")
    notices = await client.get("/notifications", headers=_auth(admin_token))
    assert any(row["type"] == "approval.decided" for row in notices.json())

    tool_approved = await client.post(
        f"/brain/tool-calls/{tool.id}/approve",
        headers=_auth(token),
        json={"approved": True, "comment": "允许进入人工发布清单"},
    )
    acceptance_rerun = await client.post(
        f"/brain/tasks/{acceptance.task_id}/rerun",
        headers=_auth(token),
        json={
            "acceptance_id": acceptance.id,
            "reason": "补充风险说明后重做",
            "rerun_scope": "current_agent",
            "ask_brain_rejudge": True,
        },
    )
    assert tool_approved.status_code == 200
    assert acceptance_rerun.status_code == 200
    repeated_tool_decision = await client.post(
        f"/brain/tool-calls/{tool.id}/approve",
        headers=_auth(token),
        json={"approved": True, "comment": "重复提交不应再次执行"},
    )
    assert repeated_tool_decision.status_code == 409
    events = list(await session.scalars(select(Event).where(Event.type == "approval.decided")))
    assert {row.payload["approval_kind"] for row in events} == {
        "gate",
        "tool_call",
        "deliverable",
    }


@pytest.mark.asyncio
async def test_legacy_pending_gate_list_does_not_leak_other_projects(
    client, admin, member, session
):
    _, project, _, gate, _, _, _ = await _approval_data(admin, member, session)
    token = await _token(client, "user@test.com", "user-pw-123")

    response = await client.get("/gates", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body] == [gate.id]
    assert {row["content_title"] for row in body} == {"真实脚本审批"}
    assert project.id > 0


@pytest.mark.asyncio
async def test_explicit_task_project_is_authoritative_for_approval_permission(
    client, admin, member, session
):
    workspace_client, visible_project, account, _, tool, _, membership = await _approval_data(
        admin, member, session
    )
    hidden_project = await session.scalar(
        select(Project).where(
            Project.client_id == workspace_client.id,
            Project.id != visible_project.id,
        )
    )
    assert hidden_project is not None
    session.add(ProjectAccount(project_id=hidden_project.id, account_id=account.id))
    membership.project_id = hidden_project.id
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")

    response = await client.post(
        f"/brain/tool-calls/{tool.id}/approve",
        headers=_auth(token),
        json={"approved": True, "comment": "只有账号关联项目权限"},
    )

    assert response.status_code == 403
