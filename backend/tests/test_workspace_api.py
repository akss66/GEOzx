"""工作区域接口测试：项目 / 账号 / 分组 CRUD + RBAC + org 隔离（async，SQLite override）。"""

import pytest
from sqlalchemy import select

from app.models import Event


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
async def test_update_account_integration_status_is_audited(client, admin, session):
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
            "integration_status": "connected",
            "auth_status": "authorized",
            "data_sync_status": "healthy",
            "note": "OAuth 已完成",
        },
    )

    assert updated.status_code == 200
    body = updated.json()
    assert body["integration_status"] == "connected"
    assert body["auth_status"] == "authorized"
    assert body["data_sync_status"] == "healthy"

    matrix = await client.get("/account-matrix", headers=headers)
    platform = next(row for row in matrix.json()["platforms"] if row["platform"] == "douyin")
    assert platform["integration_status"] == "connected"
    assert platform["auth_status"] == "authorized"
    assert platform["data_sync_status"] == "healthy"

    event = await session.scalar(
        select(Event).where(Event.type == "account.integration.updated")
    )
    assert event is not None
    assert event.payload["account_id"] == account_id
    assert event.payload["note"] == "OAuth 已完成"


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
