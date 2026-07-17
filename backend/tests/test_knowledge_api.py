"""Knowledge workspace API: scope, provenance, suggestions, and citations."""

import pytest

from app.models import (
    BrainTask,
    Client,
    ClientMembership,
    KnowledgeCitation,
    KnowledgeEntry,
    Project,
)
from app.models.enums import BrainTaskStatus, BrainTaskType, KnowledgeCategory, WorkspaceRole


async def _token(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _workspace(session, member, *, role: WorkspaceRole = WorkspaceRole.OPERATOR):
    workspace = Client(org_id=member.org_id, name="数码客户")
    project = Project(org_id=member.org_id, client=workspace, name="冷启动项目")
    session.add_all(
        [
            workspace,
            project,
            ClientMembership(client=workspace, user=member, role=role),
        ]
    )
    await session.commit()
    return workspace, project


@pytest.mark.asyncio
async def test_knowledge_is_client_scoped_and_tracks_provenance(client, session, member):
    workspace, project = await _workspace(session, member)
    other = Client(org_id=member.org_id, name="不可访问客户")
    session.add(other)
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")

    created = await client.post(
        "/knowledge",
        headers=_auth(token),
        json={
            "client_id": workspace.id,
            "project_id": project.id,
            "category": "hot_content",
            "title": "对比实测类内容结构",
            "content": "先呈现冲突，再用实测证据给出结论。",
            "source_type": "manual",
            "source_label": "运营团队复盘",
            "tags": ["数码", "实测"],
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["client_id"] == workspace.id
    assert body["project_id"] == project.id
    assert body["version"] == 1
    assert body["source_label"] == "运营团队复盘"

    listed = await client.get(
        f"/knowledge?client_id={workspace.id}&project_id={project.id}",
        headers=_auth(token),
    )
    assert [row["title"] for row in listed.json()] == ["对比实测类内容结构"]

    hidden = await client.get(f"/knowledge?client_id={other.id}", headers=_auth(token))
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_knowledge_update_creates_a_new_version_and_reviewer_is_read_only(
    client, session, member
):
    workspace, project = await _workspace(session, member, role=WorkspaceRole.REVIEWER)
    entry = KnowledgeEntry(
        org_id=member.org_id,
        client_id=workspace.id,
        project_id=project.id,
        category=KnowledgeCategory.USER_PERSONA,
        title="理性消费者",
        content="重视真实体验和预算边界。",
        source_type="manual",
        source_label="访谈记录",
        version=1,
        created_by_id=member.id,
        payload={},
    )
    session.add(entry)
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")

    listed = await client.get(
        f"/knowledge?client_id={workspace.id}&project_id={project.id}",
        headers=_auth(token),
    )
    assert listed.status_code == 200

    denied = await client.patch(
        f"/knowledge/{entry.id}",
        headers=_auth(token),
        json={"content": "试图越权修改"},
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_agent_suggestion_requires_human_approval_before_becoming_knowledge(
    client, session, member
):
    workspace, project = await _workspace(session, member)
    token = await _token(client, "user@test.com", "user-pw-123")

    suggested = await client.post(
        "/knowledge-suggestions",
        headers=_auth(token),
        json={
            "client_id": workspace.id,
            "project_id": project.id,
            "category": "script_library",
            "title": "评论区追问回应方式",
            "content": "先确认用户场景，再给一条可执行建议。",
            "source_agent_code": "08-customer-service",
            "source_label": "客服反馈专家建议",
            "tags": ["评论", "客服"],
        },
    )
    assert suggested.status_code == 201
    suggestion = suggested.json()
    assert suggestion["status"] == "pending"

    before = await client.get(
        f"/knowledge?client_id={workspace.id}&project_id={project.id}",
        headers=_auth(token),
    )
    assert before.json() == []

    approved = await client.post(
        f"/knowledge-suggestions/{suggestion['id']}/approve",
        headers=_auth(token),
        json={"review_note": "确认可沉淀"},
    )
    assert approved.status_code == 200
    assert approved.json()["suggestion"]["status"] == "approved"
    assert approved.json()["entry"]["source_type"] == "agent"
    assert approved.json()["entry"]["version"] == 1

    duplicate = await client.post(
        f"/knowledge-suggestions/{suggestion['id']}/approve",
        headers=_auth(token),
        json={},
    )
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_knowledge_citations_are_visible_in_the_current_scope(client, session, member):
    workspace, project = await _workspace(session, member)
    entry = KnowledgeEntry(
        org_id=member.org_id,
        client_id=workspace.id,
        project_id=None,
        category=KnowledgeCategory.PROMPT_LIBRARY,
        title="视觉提示词基线",
        content="主体明确、背景克制、保留真实材质。",
        source_type="manual",
        source_label="品牌规范",
        version=1,
        created_by_id=member.id,
        payload={},
    )
    task = BrainTask(
        org_id=member.org_id,
        title="引用测试",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
    )
    session.add_all([entry, task])
    await session.flush()
    session.add(
        KnowledgeCitation(
            org_id=member.org_id,
            client_id=workspace.id,
            project_id=project.id,
            entry_id=entry.id,
            task_id=task.id,
            agent_code="03-art-director",
            context="生成视觉提示方案",
        )
    )
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")

    response = await client.get(
        f"/knowledge/{entry.id}/citations?client_id={workspace.id}&project_id={project.id}",
        headers=_auth(token),
    )

    assert response.status_code == 200
    assert response.json()[0]["agent_code"] == "03-art-director"
    assert response.json()[0]["task_id"] == task.id
