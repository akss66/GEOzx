"""工作区域接口测试：项目 / 账号 / 分组 CRUD + RBAC + org 隔离（async，SQLite override）。"""

import pytest


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
