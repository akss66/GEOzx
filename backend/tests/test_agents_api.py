import pytest
from sqlalchemy import select

from app.agents.base import AgentContext
from app.agents.positioning import PositioningAgent
from app.llm.adapters import CompletionResult
from app.models import (
    Account,
    AgentToolCall,
    BrainTask,
    Client,
    ContentItem,
    Event,
    KnowledgeEntry,
    Project,
    ProjectMembership,
)
from app.models.enums import (
    BrainTaskStatus,
    KnowledgeCategory,
    Platform,
    WorkspaceRole,
)
from app.orchestrator.agent_kernel import KernelAction, SpecialistKernelDecision
from app.schemas.deliverable import PositioningStrategyPayload


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _patch_positioning_kernel(monkeypatch, fake_run) -> None:
    async def fake_kernel_decide(self, session, org_id, ctx, **kwargs):
        payload = await fake_run(self, session, org_id, ctx)
        return SpecialistKernelDecision(
            action=KernelAction.FINISH,
            rationale="Test fixture has enough scoped evidence.",
            deliverable=payload,
        )

    monkeypatch.setattr(
        "app.agents.positioning.PositioningAgent.kernel_decide",
        fake_kernel_decide,
    )


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
async def test_expert_management_is_admin_only_and_hides_model_infrastructure(
    client, admin, member
):
    admin_token = await _token(client, "admin@test.com", "admin-pw-123")
    member_token = await _token(client, "user@test.com", "user-pw-123")

    denied = await client.get("/agents/management", headers=_auth(member_token))
    listing = await client.get("/agents/management", headers=_auth(admin_token))

    assert denied.status_code == 403
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 9
    assert rows[0]["code"] == "00-decision"
    assert rows[0]["enabled"] is True
    assert "responsibility" in rows[0]
    assert "tool_permissions" in rows[0]
    assert "quality_gates" in rows[0]
    serialized = str(rows)
    assert "primary_model" not in serialized
    assert "fallback_model" not in serialized
    assert "client_secret" not in serialized


@pytest.mark.asyncio
async def test_admin_can_persist_expert_policy_with_audit_event(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")

    updated = await client.put(
        "/agents/02-content-director/management",
        headers=_auth(token),
        json={
            "enabled": True,
            "responsibility": "围绕当前账号定位产出可拍摄、可审核的抖音脚本。",
            "system_prompt": "优先使用真实账号上下文，不编造产品参数。",
            "tool_permissions": {
                "brief_builder": "auto",
                "compliance_precheck": "confirm",
            },
            "quality_gates": ["topic_review", "script_compliance"],
        },
    )

    assert updated.status_code == 200
    body = updated.json()
    assert body["responsibility"] == "围绕当前账号定位产出可拍摄、可审核的抖音脚本。"
    assert body["system_prompt"] == "优先使用真实账号上下文，不编造产品参数。"
    assert body["tool_permissions"] == {
        "brief_builder": "auto",
        "compliance_precheck": "confirm",
    }
    assert body["quality_gates"] == ["topic_review", "script_compliance"]

    events = list(
        await session.scalars(
            select(Event).where(Event.type == "expert.management.updated")
        )
    )
    assert len(events) == 1
    assert events[0].payload["agent_code"] == "02-content-director"
    assert events[0].payload["updated_by"] == admin.id


@pytest.mark.asyncio
async def test_expert_management_rejects_unknown_tools_and_quality_gates(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")

    response = await client.put(
        "/agents/02-content-director/management",
        headers=_auth(token),
        json={
            "enabled": True,
            "responsibility": "负责内容策划。",
            "system_prompt": "",
            "tool_permissions": {"invented_tool": "auto"},
            "quality_gates": ["invented_gate"],
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_disabled_expert_cannot_be_invoked_directly(client, admin, session):
    project, account = await _direct_context(session, admin)
    token = await _token(client, "admin@test.com", "admin-pw-123")
    configured = await client.put(
        "/agents/01-positioning/management",
        headers=_auth(token),
        json={
            "enabled": False,
            "responsibility": "负责账号定位。",
            "system_prompt": "",
            "tool_permissions": {
                "account_context": "auto",
                "profile_snapshot": "auto",
            },
            "quality_gates": ["positioning_review"],
        },
    )
    assert configured.status_code == 200

    response = await client.post(
        "/agents/01-positioning/invoke",
        headers=_auth(token),
        json={
            "prompt": "重新判断账号定位",
            "project_id": project.id,
            "account_id": account.id,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "该专家已停用"


@pytest.mark.asyncio
async def test_expert_prompt_addition_is_applied_to_real_agent_runtime(
    client, admin, session
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    updated = await client.put(
        "/agents/01-positioning/management",
        headers=_auth(token),
        json={
            "enabled": True,
            "responsibility": "负责账号定位。",
            "system_prompt": "只使用当前账号的真实资料，不允许编造粉丝数据。",
            "tool_permissions": {
                "account_context": "auto",
                "profile_snapshot": "auto",
                "knowledge_search": "auto",
            },
            "quality_gates": ["positioning_review"],
        },
    )
    assert updated.status_code == 200

    class FakeLLM:
        messages = None

        async def chat(self, session, org_id, agent_code, messages):
            self.messages = messages
            return (
                CompletionResult(
                    content=(
                        '{"account_persona":"真实体验官","target_audience":"理性消费者",'
                        '"differentiation":["真实","透明"],'
                        '"content_pillars":["实测","选购建议"]}'
                    ),
                    model="test-model",
                    prompt_tokens=10,
                    completion_tokens=10,
                    total_tokens=20,
                ),
                0.0,
            )

    llm = FakeLLM()
    agent = PositioningAgent(llm=llm)
    await agent.run(
        session,
        admin.org_id,
        AgentContext(content_item_id=1, request="分析定位"),
    )

    assert "本组织专家补充指令" in llm.messages[0]["content"]
    assert "不允许编造粉丝数据" in llm.messages[0]["content"]


async def _direct_context(session, admin, member=None):
    workspace = Client(org_id=admin.org_id, name="独立专家客户")
    project = Project(org_id=admin.org_id, client=workspace, name="独立专家项目")
    session.add_all([workspace, project])
    await session.flush()
    account = Account(
        org_id=admin.org_id,
        client_id=workspace.id,
        project_id=project.id,
        platform=Platform.DOUYIN,
        nickname="数码菌",
        auth={"auth_status": "authorized", "data_sync_status": "healthy"},
    )
    session.add(account)
    if member is not None:
        session.add(
            ProjectMembership(
                project_id=project.id,
                user_id=member.id,
                role=WorkspaceRole.REVIEWER,
            )
        )
    await session.commit()
    await session.refresh(account)
    return project, account


@pytest.mark.asyncio
async def test_direct_agent_run_requires_explicit_project_and_account(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")

    response = await client.post(
        "/agents/01-positioning/invoke",
        headers=_auth(token),
        json={"prompt": "重新判断账号定位"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_direct_agent_run_creates_real_artifact_and_pending_acceptance(
    client, admin, member, session, monkeypatch
):
    project, account = await _direct_context(session, admin, member)
    knowledge = KnowledgeEntry(
        org_id=admin.org_id,
        client_id=project.client_id,
        project_id=project.id,
        category=KnowledgeCategory.USER_PERSONA,
        title="理性数码用户画像",
        content="用户重视真实体验、预算和长期使用价值。",
        source_type="manual",
        source_label="用户访谈",
        version=1,
        created_by_id=admin.id,
        payload={},
    )
    session.add(knowledge)
    await session.commit()

    async def fake_run(self, session, org_id, ctx):
        assert ctx.request == "重新判断账号定位"
        assert ctx.upstream["account_context"]["account_id"] == account.id
        assert ctx.knowledge["user_persona"][0]["title"] == "理性数码用户画像"
        return PositioningStrategyPayload(
            account_persona="敢说真话的数码评测账号",
            target_audience="关注真实体验和理性选购的数码用户",
            differentiation=["拒绝参数堆砌", "优先真实体验"],
            content_pillars=["产品实测", "选购建议"],
        )

    _patch_positioning_kernel(monkeypatch, fake_run)
    token = await _token(client, "admin@test.com", "admin-pw-123")
    response = await client.post(
        "/agents/01-positioning/invoke",
        headers=_auth(token),
        json={
            "prompt": "重新判断账号定位",
            "project_id": project.id,
            "account_id": account.id,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["task"]["runtime_mode"] == "direct_agent"
    assert body["task"]["brief"]["project_id"] == project.id
    assert body["task"]["brief"]["account_ids"] == [account.id]
    assert body["invocation"]["status"] == "done"
    assert body["deliverable"]["payload"]["account_persona"] == "敢说真话的数码评测账号"
    assert body["acceptance"]["status"] == "pending"
    assert "敢说真话" in body["acceptance"]["summary"]
    assert body["knowledge_sources"] == [
        {
            "id": knowledge.id,
            "category": "user_persona",
            "title": "理性数码用户画像",
            "source_label": "用户访谈",
            "version": 1,
        }
    ]
    stored_task = await session.get(BrainTask, body["task"]["id"])
    assert stored_task is not None
    stored_content = await session.get(ContentItem, stored_task.content_item_id)
    assert stored_task.created_by_id == admin.id
    assert stored_content is not None and stored_content.created_by_id == admin.id

    runs = await client.get(
        "/agents/01-positioning/runs",
        headers=_auth(token),
        params={"project_id": project.id, "account_id": account.id},
    )
    assert runs.status_code == 200
    assert [row["task"]["id"] for row in runs.json()] == [body["task"]["id"]]

    suggested = await client.post(
        f"/agents/runs/{body['task']['id']}/knowledge-suggestion",
        headers=_auth(token),
    )
    assert suggested.status_code == 200
    assert suggested.json()["status"] == "pending"
    assert suggested.json()["category"] == "user_persona"
    repeated = await client.post(
        f"/agents/runs/{body['task']['id']}/knowledge-suggestion",
        headers=_auth(token),
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == suggested.json()["id"]
    reviewer_token = await _token(client, "user@test.com", "user-pw-123")
    reviewer_attempt = await client.post(
        f"/agents/runs/{body['task']['id']}/knowledge-suggestion",
        headers=_auth(reviewer_token),
    )
    assert reviewer_attempt.status_code == 403
    official = await client.get(
        f"/knowledge?client_id={project.client_id}&project_id={project.id}",
        headers=_auth(token),
    )
    assert [row["id"] for row in official.json()] == [knowledge.id]


@pytest.mark.asyncio
async def test_direct_agent_run_rejects_reviewer_and_cross_project_account(
    client, admin, member, session
):
    project, account = await _direct_context(session, admin, member)
    other_project = Project(org_id=admin.org_id, name="其他项目")
    session.add(other_project)
    await session.commit()
    reviewer_token = await _token(client, "user@test.com", "user-pw-123")
    admin_token = await _token(client, "admin@test.com", "admin-pw-123")

    denied = await client.post(
        "/agents/01-positioning/invoke",
        headers=_auth(reviewer_token),
        json={
            "prompt": "修改定位",
            "project_id": project.id,
            "account_id": account.id,
        },
    )
    unlinked = await client.post(
        "/agents/01-positioning/invoke",
        headers=_auth(admin_token),
        json={
            "prompt": "修改定位",
            "project_id": other_project.id,
            "account_id": account.id,
        },
    )

    assert denied.status_code == 403
    assert unlinked.status_code == 400


@pytest.mark.asyncio
async def test_direct_agent_handoff_returns_audited_main_agent_draft(
    client, admin, session, monkeypatch
):
    project, account = await _direct_context(session, admin)

    async def fake_run(self, session, org_id, ctx):
        return PositioningStrategyPayload(
            account_persona="真实数码体验官",
            target_audience="理性数码消费者",
            differentiation=["真实体验", "长期跟踪"],
            content_pillars=["实测", "选购建议"],
        )

    _patch_positioning_kernel(monkeypatch, fake_run)
    token = await _token(client, "admin@test.com", "admin-pw-123")
    created = await client.post(
        "/agents/01-positioning/invoke",
        headers=_auth(token),
        json={
            "prompt": "判断账号定位",
            "project_id": project.id,
            "account_id": account.id,
        },
    )
    task_id = created.json()["task"]["id"]

    handed_off = await client.post(
        f"/agents/runs/{task_id}/handoff",
        headers=_auth(token),
    )

    assert handed_off.status_code == 200
    assert handed_off.json()["task_id"] == task_id
    assert "真实数码体验官" in handed_off.json()["prompt"]
    assert handed_off.json()["project_id"] == project.id
    assert handed_off.json()["account_id"] == account.id
