import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AgentTask,
    Client,
    ContentItem,
    Deliverable,
    MaterialAsset,
    Project,
    ProjectMembership,
)
from app.models.enums import (
    AgentTaskStatus,
    ContentStage,
    DeliverableStatus,
    DeliverableType,
    MaterialStatus,
    Platform,
    WorkspaceRole,
)


async def _token(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_content_list_and_workspace_follow_project_membership(client, admin, member, session):
    workspace = Client(org_id=admin.org_id, name="可见客户")
    session.add(workspace)
    await session.flush()
    visible_project = Project(
        org_id=admin.org_id,
        client_id=workspace.id,
        name="可见内容项目",
    )
    hidden_project = Project(
        org_id=admin.org_id,
        client_id=workspace.id,
        name="隐藏内容项目",
    )
    session.add_all([visible_project, hidden_project])
    await session.flush()
    account = Account(
        org_id=admin.org_id,
        client_id=workspace.id,
        project_id=visible_project.id,
        platform=Platform.DOUYIN,
        nickname="内容账号",
    )
    visible = ContentItem(
        project_id=visible_project.id,
        account_id=None,
        title="可见脚本",
        current_stage=ContentStage.CONTENT_DIRECTION,
    )
    hidden = ContentItem(project_id=hidden_project.id, title="隐藏脚本")
    session.add_all([account, visible, hidden])
    await session.flush()
    visible.account_id = account.id
    deliverable = Deliverable(
        content_item_id=visible.id,
        agent_code="02-content",
        type=DeliverableType.VIDEO_SCRIPT,
        version=1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={
            "title": "真实脚本",
            "hook": "前三秒钩子",
            "scenes": ["开场", "实测", "结论"],
            "duration_seconds": 45,
            "bgm_suggestion": "轻快节奏",
        },
    )
    session.add(deliverable)
    await session.flush()
    session.add_all(
        [
            AgentTask(
                content_item_id=visible.id,
                agent_code="02-content",
                stage=ContentStage.CONTENT_DIRECTION,
                status=AgentTaskStatus.DONE,
                output_deliverable_id=deliverable.id,
            ),
            MaterialAsset(
                org_id=admin.org_id,
                content_item_id=visible.id,
                deliverable_id=deliverable.id,
                kind="image",
                status=MaterialStatus.READY,
                local_path="content/cover.png",
            ),
            ProjectMembership(
                project_id=visible_project.id,
                user_id=member.id,
                role=WorkspaceRole.EDITOR,
            ),
        ]
    )
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")
    headers = _auth(token)

    listing = await client.get("/content-items", headers=headers)
    workspace_response = await client.get(f"/content-items/{visible.id}/workspace", headers=headers)
    hidden_response = await client.get(f"/content-items/{hidden.id}/workspace", headers=headers)

    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == [visible.id]
    assert workspace_response.status_code == 200
    body = workspace_response.json()
    assert body["project_name"] == "可见内容项目"
    assert body["account"]["nickname"] == "内容账号"
    assert body["deliverables"][0]["payload"]["hook"] == "前三秒钩子"
    assert body["materials"][0]["kind"] == "image"
    assert body["tasks"][0]["status"] == "done"
    assert body["publish_tool_calls"] == []
    assert hidden_response.status_code == 404


@pytest.mark.asyncio
async def test_selected_account_scope_hides_content_routes(client, admin, member, session):
    workspace = Client(org_id=admin.org_id, name="Scoped content client")
    project = Project(org_id=admin.org_id, client=workspace, name="Scoped content project")
    account = Account(
        org_id=admin.org_id,
        client=workspace,
        project=project,
        platform=Platform.DOUYIN,
        nickname="Hidden content account",
    )
    content = ContentItem(project=project, title="Hidden account content")
    member.account_scope_mode = "selected"
    session.add_all(
        [
            workspace,
            project,
            account,
            content,
            ProjectMembership(
                project=project,
                user=member,
                role=WorkspaceRole.EDITOR,
            ),
        ]
    )
    await session.flush()
    content.account_id = account.id
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")
    headers = _auth(token)

    created = await client.post(
        "/content-items",
        headers=headers,
        json={"project_id": project.id, "account_id": account.id, "title": "Denied content"},
    )
    listing = await client.get("/content-items", headers=headers)
    workspace_response = await client.get(f"/content-items/{content.id}/workspace", headers=headers)
    readiness = await client.post(
        f"/content-items/{content.id}/publish-readiness",
        headers=headers,
        json={"platform": "douyin", "title": "Denied publish"},
    )

    assert created.status_code == 404
    assert listing.status_code == 200
    assert listing.json() == []
    assert workspace_response.status_code == 404
    assert readiness.status_code == 404


@pytest.mark.asyncio
async def test_deliverable_revision_creates_version_and_preserves_history(client, admin, session):
    project = Project(org_id=admin.org_id, name="内容修订项目")
    content = ContentItem(project=project, title="脚本修订")
    original = Deliverable(
        content_item=content,
        agent_code="02-content",
        type=DeliverableType.VIDEO_SCRIPT,
        version=1,
        status=DeliverableStatus.APPROVED,
        payload={
            "title": "第一版",
            "hook": "旧钩子",
            "scenes": ["旧场景"],
            "duration_seconds": 30,
            "bgm_suggestion": None,
        },
    )
    session.add_all([project, content, original])
    await session.commit()
    token = await _token(client, "admin@test.com", "admin-pw-123")

    response = await client.post(
        f"/deliverables/{original.id}/revisions",
        headers=_auth(token),
        json={
            "payload": {
                "title": "第二版",
                "hook": "新钩子",
                "scenes": ["开场", "实测"],
                "duration_seconds": 40,
                "bgm_suggestion": "电子节奏",
            },
            "note": "用户在内容画布中修订",
        },
    )

    assert response.status_code == 201
    assert response.json()["version"] == 2
    assert response.json()["status"] == "pending_review"
    rows = list(
        await session.scalars(
            select(Deliverable)
            .where(Deliverable.content_item_id == content.id)
            .order_by(Deliverable.version)
        )
    )
    assert [row.version for row in rows] == [1, 2]
    assert rows[0].status == DeliverableStatus.SUPERSEDED
    assert rows[1].payload["hook"] == "新钩子"


@pytest.mark.asyncio
async def test_deliverable_revision_rejects_invalid_payload(client, admin, session):
    project = Project(org_id=admin.org_id, name="校验项目")
    content = ContentItem(project=project, title="校验脚本")
    original = Deliverable(
        content_item=content,
        agent_code="02-content",
        type=DeliverableType.VIDEO_SCRIPT,
        version=1,
        status=DeliverableStatus.APPROVED,
        payload={
            "title": "第一版",
            "hook": "旧钩子",
            "scenes": ["旧场景"],
            "duration_seconds": 30,
            "bgm_suggestion": None,
        },
    )
    session.add_all([project, content, original])
    await session.commit()
    token = await _token(client, "admin@test.com", "admin-pw-123")

    response = await client.post(
        f"/deliverables/{original.id}/revisions",
        headers=_auth(token),
        json={"payload": {"title": "缺少字段"}},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_content_creation_rejects_account_from_another_project(client, admin, session):
    first = Project(org_id=admin.org_id, name="内容项目 A")
    second = Project(org_id=admin.org_id, name="内容项目 B")
    session.add_all([first, second])
    await session.flush()
    account = Account(
        org_id=admin.org_id,
        project_id=first.id,
        platform=Platform.DOUYIN,
        nickname="项目 A 账号",
    )
    session.add(account)
    await session.commit()
    token = await _token(client, "admin@test.com", "admin-pw-123")

    response = await client.post(
        "/content-items",
        headers=_auth(token),
        json={
            "project_id": second.id,
            "account_id": account.id,
            "title": "错误账号上下文",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "账号未绑定当前项目"
