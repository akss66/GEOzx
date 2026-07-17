"""工作区域接口测试：项目 / 账号 / 分组 CRUD + RBAC + org 隔离（async，SQLite override）。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import (
    Account,
    AccountGroup,
    BrainTask,
    Client,
    ClientMembership,
    ContentItem,
    DeliverableAcceptance,
    Event,
    PlatformAccountAuth,
    Project,
    ProjectMembership,
    TaskBrief,
)
from app.models.enums import (
    AgentCode,
    BrainTaskStatus,
    DeliverableAcceptanceStatus,
    DeliverableType,
    GroupDimension,
    Platform,
    WorkspaceRole,
)


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# —— 项目 ——


@pytest.mark.asyncio
async def test_admin_creates_and_lists_project(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    resp = await client.post(
        "/projects", headers=_auth(token), json={"name": "618 大促", "description": "数码专场"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "618 大促"
    assert body["status"] == "active"

    listing = await client.get("/projects", headers=_auth(token))
    assert listing.status_code == 200
    assert any(p["name"] == "618 大促" for p in listing.json())


@pytest.mark.asyncio
async def test_user_can_list_but_not_create_project(client, member):
    token = await _token(client, "user@test.com", "user-pw-123")
    assert (await client.get("/projects", headers=_auth(token))).status_code == 200
    resp = await client.post("/projects", headers=_auth(token), json={"name": "X"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_member_account_matrix_is_limited_to_assigned_clients(
    client, admin, member, session
):
    visible_client = Client(org_id=admin.org_id, name="可见客户")
    hidden_client = Client(org_id=admin.org_id, name="隐藏客户")
    visible_group = AccountGroup(
        org_id=admin.org_id, name="可见分组", dimension=GroupDimension.TRACK
    )
    hidden_group = AccountGroup(
        org_id=admin.org_id, name="隐藏分组", dimension=GroupDimension.PERSONA
    )
    session.add_all([visible_client, hidden_client, visible_group, hidden_group])
    await session.flush()
    visible_account = Account(
        org_id=admin.org_id,
        client_id=visible_client.id,
        nickname="可见账号",
        platform=Platform.DOUYIN,
        group_id=visible_group.id,
    )
    hidden_account = Account(
        org_id=admin.org_id,
        client_id=hidden_client.id,
        nickname="隐藏账号",
        platform=Platform.DOUYIN,
        group_id=hidden_group.id,
    )
    session.add_all(
        [
            visible_account,
            hidden_account,
            ClientMembership(
                client_id=visible_client.id,
                user_id=member.id,
                role=WorkspaceRole.OPERATOR,
            ),
        ]
    )
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")
    headers = _auth(token)

    accounts = await client.get("/accounts", headers=headers)
    matrix = await client.get("/account-matrix", headers=headers)
    groups = await client.get("/account-groups", headers=headers)

    assert accounts.status_code == 200
    assert [row["nickname"] for row in accounts.json()] == ["可见账号"]
    assert matrix.status_code == 200
    matrix_names = [
        row["nickname"]
        for group in matrix.json()["groups"]
        for row in group["accounts"]
    ]
    assert matrix_names == ["可见账号"]
    assert [row["name"] for row in groups.json()] == ["可见分组"]


@pytest.mark.asyncio
async def test_project_scoped_member_only_sees_accounts_in_assigned_project(
    client, admin, member, session
):
    workspace = Client(org_id=admin.org_id, name="项目客户")
    session.add(workspace)
    await session.flush()
    visible_project = Project(org_id=admin.org_id, client_id=workspace.id, name="可见项目")
    hidden_project = Project(org_id=admin.org_id, client_id=workspace.id, name="隐藏项目")
    session.add_all([visible_project, hidden_project])
    await session.flush()
    session.add_all(
        [
            Account(
                org_id=admin.org_id,
                client_id=workspace.id,
                project_id=visible_project.id,
                nickname="项目可见账号",
                platform=Platform.DOUYIN,
            ),
            Account(
                org_id=admin.org_id,
                client_id=workspace.id,
                project_id=hidden_project.id,
                nickname="项目隐藏账号",
                platform=Platform.DOUYIN,
            ),
            Account(
                org_id=admin.org_id,
                client_id=workspace.id,
                nickname="客户未绑定账号",
                platform=Platform.DOUYIN,
            ),
            ProjectMembership(
                project_id=visible_project.id,
                user_id=member.id,
                role=WorkspaceRole.OPERATOR,
            ),
        ]
    )
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")

    response = await client.get("/accounts", headers=_auth(token))
    hidden_filter = await client.get(
        f"/accounts?project_id={hidden_project.id}", headers=_auth(token)
    )

    assert response.status_code == 200
    assert [row["nickname"] for row in response.json()] == ["项目可见账号"]
    assert hidden_filter.status_code == 404


@pytest.mark.asyncio
async def test_update_and_archive_project(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    pid = (
        await client.post("/projects", headers=_auth(token), json={"name": "草稿项目"})
    ).json()["id"]

    upd = await client.patch(
        f"/projects/{pid}", headers=_auth(token), json={"name": "正式项目", "status": "paused"}
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "正式项目"
    assert upd.json()["status"] == "paused"

    # 删除 = 软归档
    assert (await client.delete(f"/projects/{pid}", headers=_auth(token))).status_code == 204
    after = await client.get("/projects", headers=_auth(token))
    archived = next(p for p in after.json() if p["id"] == pid)
    assert archived["status"] == "archived"


# —— 账号分组 + 账号 ——


@pytest.mark.asyncio
async def test_account_group_and_account_crud(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    gid = (
        await client.post(
            "/account-groups", headers=_auth(token), json={"name": "数码科技", "dimension": "track"}
        )
    ).json()["id"]

    create = await client.post(
        "/accounts",
        headers=_auth(token),
        json={"nickname": "数码菌", "platform": "douyin", "group_id": gid},
    )
    assert create.status_code == 201
    aid = create.json()["id"]
    assert create.json()["group_id"] == gid

    # 按分组过滤
    filtered = await client.get(f"/accounts?group_id={gid}", headers=_auth(token))
    assert [a["id"] for a in filtered.json()] == [aid]

    # 更新状态
    upd = await client.patch(
        f"/accounts/{aid}", headers=_auth(token), json={"status": "inactive"}
    )
    assert upd.status_code == 200
    assert upd.json()["status"] == "inactive"

    # 删除
    assert (await client.delete(f"/accounts/{aid}", headers=_auth(token))).status_code == 204
    assert (await client.get("/accounts", headers=_auth(token))).json() == []


@pytest.mark.asyncio
async def test_admin_batch_updates_account_ownership_and_status(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    group_id = (
        await client.post(
            "/account-groups",
            headers=headers,
            json={"name": "批量运营组", "dimension": "track"},
        )
    ).json()["id"]
    project_id = (
        await client.post("/projects", headers=headers, json={"name": "批量项目"})
    ).json()["id"]
    account_ids = []
    for nickname in ("矩阵一号", "矩阵二号"):
        account_ids.append(
            (
                await client.post(
                    "/accounts",
                    headers=headers,
                    json={"nickname": nickname, "platform": "douyin"},
                )
            ).json()["id"]
        )

    response = await client.patch(
        "/accounts/batch",
        headers=headers,
        json={
            "account_ids": account_ids,
            "group_id": group_id,
            "project_id": project_id,
            "status": "inactive",
        },
    )

    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == account_ids
    assert all(row["group_id"] == group_id for row in response.json())
    assert all(row["project_id"] == project_id for row in response.json())
    assert all(project_id in row["project_ids"] for row in response.json())
    assert all(row["status"] == "inactive" for row in response.json())

    event = await session.scalar(select(Event).where(Event.type == "accounts.batch_updated"))
    assert event is not None
    assert event.payload["account_ids"] == account_ids
    assert event.payload["group_id"] == group_id
    assert event.payload["project_id"] == project_id


@pytest.mark.asyncio
async def test_account_matrix_groups_accounts_and_platform_status(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    project_a = (
        await client.post("/projects", headers=headers, json={"name": "户外品牌"})
    ).json()["id"]
    project_b = (
        await client.post("/projects", headers=headers, json={"name": "数码品牌"})
    ).json()["id"]
    gid = (
        await client.post(
            "/account-groups",
            headers=headers,
            json={"name": "户外矩阵", "dimension": "persona"},
        )
    ).json()["id"]
    await client.post(
        "/accounts",
        headers=headers,
        json={
            "nickname": "露营一号",
            "platform": "douyin",
            "group_id": gid,
            "project_id": project_a,
        },
    )
    await client.post(
        "/accounts",
        headers=headers,
        json={
            "nickname": "小红书手动号",
            "platform": "xiaohongshu",
            "project_id": project_b,
        },
    )

    resp = await client.get("/account-matrix", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["groups"][0]["name"] == "户外矩阵"
    assert body["groups"][0]["accounts"][0]["nickname"] == "露营一号"
    assert body["ungrouped_accounts"][0]["nickname"] == "小红书手动号"
    assert {row["platform"]: row["total"] for row in body["platforms"]} == {
        "douyin": 1,
        "xiaohongshu": 1,
    }

    project_filtered = await client.get(f"/account-matrix?project_id={project_a}", headers=headers)
    assert project_filtered.status_code == 200
    filtered_body = project_filtered.json()
    assert filtered_body["groups"][0]["accounts"][0]["nickname"] == "露营一号"
    assert filtered_body["ungrouped_accounts"] == []
    assert {row["platform"]: row["total"] for row in filtered_body["platforms"]} == {
        "douyin": 1
    }

    account_filtered = await client.get(f"/accounts?project_id={project_a}", headers=headers)
    assert [a["nickname"] for a in account_filtered.json()] == ["露营一号"]


@pytest.mark.asyncio
async def test_manual_account_integration_status_is_audited(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "抖音授权号", "platform": "douyin"},
        )
    ).json()["id"]

    updated = await client.patch(
        f"/accounts/{account_id}/integration",
        headers=headers,
        json={
            "integration_status": "manual",
            "auth_status": "manual",
            "data_sync_status": "manual",
            "note": "本地开发模式",
        },
    )

    assert updated.status_code == 200
    body = updated.json()
    assert body["integration_status"] == "manual"
    assert body["auth_status"] == "manual"
    assert body["data_sync_status"] == "manual"
    assert body["publish_capability"] == "manual_only"

    matrix = await client.get("/account-matrix", headers=headers)
    platform = next(row for row in matrix.json()["platforms"] if row["platform"] == "douyin")
    assert platform["integration_status"] == "manual"
    assert platform["auth_status"] == "manual"
    assert platform["data_sync_status"] == "manual"

    event = await session.scalar(
        select(Event).where(Event.type == "account.integration.updated")
    )
    assert event is not None
    assert event.payload["account_id"] == account_id
    assert event.payload["note"] == "本地开发模式"


@pytest.mark.asyncio
async def test_account_integration_rejects_forged_official_status(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "未授权抖音号", "platform": "douyin"},
        )
    ).json()["id"]

    updated = await client.patch(
        f"/accounts/{account_id}/integration",
        headers=headers,
        json={
            "integration_status": "connected",
            "auth_status": "authorized",
            "data_sync_status": "healthy",
        },
    )

    assert updated.status_code == 409
    assert updated.json()["detail"] == "官方授权和同步状态只能由平台回调或同步任务更新"


@pytest.mark.asyncio
async def test_account_integration_cannot_replace_official_status_with_manual(
    client, admin, session
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "正式授权号", "platform": "douyin"},
        )
    ).json()["id"]
    account = await session.get(Account, account_id)
    assert account is not None
    account.auth = {
        "integration_status": "connected",
        "auth_status": "authorized",
        "data_sync_status": "healthy",
    }
    await session.commit()

    updated = await client.patch(
        f"/accounts/{account_id}/integration",
        headers=headers,
        json={
            "integration_status": "manual",
            "auth_status": "manual",
            "data_sync_status": "manual",
        },
    )

    assert updated.status_code == 409
    assert updated.json()["detail"] == "已存在官方接入状态，不能切换为开发模式"


@pytest.mark.asyncio
async def test_account_list_includes_real_operational_context(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "运营账号", "platform": "douyin"},
        )
    ).json()["id"]
    account = await session.get(Account, account_id)
    assert account is not None
    account.auth = {
        "integration_status": "connected",
        "auth_status": "authorized",
        "data_sync_status": "healthy",
    }
    auth = PlatformAccountAuth(
        org_id=admin.org_id,
        account_id=account_id,
        platform="douyin",
        auth_status="authorized",
        data_sync_status="healthy",
        raw_profile={"avatar": "https://example.com/avatar.png"},
        last_sync_at=datetime(2026, 7, 16, 8, 30, tzinfo=UTC),
    )
    session.add(auth)

    historical_task = BrainTask(
        org_id=admin.org_id,
        title="历史待验收任务",
        status=BrainTaskStatus.PENDING_ACCEPTANCE,
        progress=38,
        current_focus="等待历史交付物验收",
        risk_count=7,
    )
    historical_task.brief = TaskBrief(
        goal="历史任务",
        platforms=["douyin"],
        account_ids=[account_id],
    )
    session.add(historical_task)

    task = BrainTask(
        org_id=admin.org_id,
        title="诊断账号定位",
        status=BrainTaskStatus.RUNNING,
        progress=45,
        current_focus="账号定位专家正在分析",
        risk_count=2,
    )
    task.brief = TaskBrief(
        goal="诊断账号定位",
        platforms=["douyin"],
        account_ids=[account_id],
    )
    task.acceptances.append(
        DeliverableAcceptance(
            agent_code=AgentCode.POSITIONING,
            agent_name="账号定位专家",
            deliverable_type=DeliverableType.POSITIONING_STRATEGY,
            title="账号定位诊断",
            summary="聚焦理性数码消费与真实测评。",
            status=DeliverableAcceptanceStatus.APPROVED,
        )
    )
    session.add(task)
    await session.commit()

    listing = await client.get("/accounts", headers=headers)

    assert listing.status_code == 200
    account = next(row for row in listing.json() if row["id"] == account_id)
    assert account["avatar_url"] == "https://example.com/avatar.png"
    assert account["positioning_summary"] == "聚焦理性数码消费与真实测评。"
    assert account["current_task"] == {
        "id": task.id,
        "title": "诊断账号定位",
        "status": "running",
        "progress": 45,
        "current_focus": "账号定位专家正在分析",
    }
    assert account["risk_count"] == 2
    assert account["last_sync_at"].startswith("2026-07-16T08:30:00")
    assert account["publish_capability"] == "prepare_only"


@pytest.mark.asyncio
async def test_distribution_action_writes_audit_event(client, member, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "分发账号", "platform": "douyin"},
        )
    ).json()["id"]
    account = await session.get(Account, account_id)
    assert account is not None and account.client_id is not None
    session.add(
        ClientMembership(
            client_id=account.client_id,
            user_id=member.id,
            role=WorkspaceRole.OPERATOR,
        )
    )
    await session.commit()

    member_token = await _token(client, "user@test.com", "user-pw-123")
    member_headers = _auth(member_token)
    created = await client.post(
        "/distribution/actions",
        headers=member_headers,
        json={
            "platform": "douyin",
            "account_ids": [account_id],
            "action_type": "manual_publish",
            "note": "已在抖音后台排期",
        },
    )

    assert created.status_code == 201
    body = created.json()
    assert body["platform"] == "douyin"
    assert body["account_ids"] == [account_id]
    assert body["status"] == "recorded"

    event = await session.scalar(select(Event).where(Event.type == "distribution.action"))
    assert event is not None
    assert event.payload["account_ids"] == [account_id]
    assert event.payload["created_by"] == member.id
    assert event.payload["note"] == "已在抖音后台排期"


@pytest.mark.asyncio
async def test_distribution_action_hides_unassigned_account(client, admin, member):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    account_id = (
        await client.post(
            "/accounts",
            headers=_auth(token),
            json={"nickname": "其他客户账号", "platform": "douyin"},
        )
    ).json()["id"]
    member_token = await _token(client, "user@test.com", "user-pw-123")

    response = await client.post(
        "/distribution/actions",
        headers=_auth(member_token),
        json={"platform": "douyin", "account_ids": [account_id]},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_distribution_action_hides_unassigned_content_item(
    client, admin, member, session
):
    visible_client = Client(org_id=admin.org_id, name="可操作客户")
    hidden_client = Client(org_id=admin.org_id, name="其他客户")
    session.add_all([visible_client, hidden_client])
    await session.flush()
    hidden_project = Project(
        org_id=admin.org_id,
        client_id=hidden_client.id,
        name="其他客户项目",
    )
    visible_account = Account(
        org_id=admin.org_id,
        client_id=visible_client.id,
        nickname="可操作账号",
        platform=Platform.DOUYIN,
    )
    session.add_all([hidden_project, visible_account])
    await session.flush()
    hidden_content = ContentItem(project_id=hidden_project.id, title="不可见内容")
    session.add_all(
        [
            hidden_content,
            ClientMembership(
                client_id=visible_client.id,
                user_id=member.id,
                role=WorkspaceRole.OPERATOR,
            ),
        ]
    )
    await session.commit()
    member_token = await _token(client, "user@test.com", "user-pw-123")

    response = await client.post(
        "/distribution/actions",
        headers=_auth(member_token),
        json={
            "platform": "douyin",
            "account_ids": [visible_account.id],
            "content_item_id": hidden_content.id,
        },
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reviewer_can_view_but_cannot_record_distribution(
    client, admin, member, session
):
    workspace = Client(org_id=admin.org_id, name="审核客户")
    session.add(workspace)
    await session.flush()
    account = Account(
        org_id=admin.org_id,
        client_id=workspace.id,
        nickname="待审核账号",
        platform=Platform.DOUYIN,
    )
    session.add_all(
        [
            account,
            ClientMembership(
                client_id=workspace.id,
                user_id=member.id,
                role=WorkspaceRole.REVIEWER,
            ),
        ]
    )
    await session.commit()
    member_token = await _token(client, "user@test.com", "user-pw-123")
    headers = _auth(member_token)

    listing = await client.get("/accounts", headers=headers)
    response = await client.post(
        "/distribution/actions",
        headers=headers,
        json={"platform": "douyin", "account_ids": [account.id]},
    )

    assert [row["id"] for row in listing.json()] == [account.id]
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_distribution_action_rejects_platform_mismatch(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "小红书账号", "platform": "xiaohongshu"},
        )
    ).json()["id"]

    resp = await client.post(
        "/distribution/actions",
        headers=headers,
        json={"platform": "douyin", "account_ids": [account_id]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_user_cannot_create_account(client, member):
    token = await _token(client, "user@test.com", "user-pw-123")
    resp = await client.post(
        "/accounts", headers=_auth(token), json={"nickname": "x", "platform": "douyin"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_account_with_unknown_group_404(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    resp = await client.post(
        "/accounts",
        headers=_auth(token),
        json={"nickname": "孤儿号", "platform": "douyin", "group_id": 99999},
    )
    assert resp.status_code == 404
