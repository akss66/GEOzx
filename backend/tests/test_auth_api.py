"""认证 + RBAC 接口测试（async，SQLite override）。"""

import pytest


async def _login(client, email: str, password: str):
    return await client.post("/auth/login", json={"email": email, "password": password})


@pytest.mark.asyncio
async def test_login_success(client, admin):
    resp = await _login(client, "admin@test.com", "admin-pw-123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["role"] == "admin"


@pytest.mark.asyncio
async def test_login_wrong_password(client, admin):
    resp = await _login(client, "admin@test.com", "nope")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_token(client, admin):
    token = (await _login(client, "admin@test.com", "admin-pw-123")).json()["access_token"]
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@test.com"


@pytest.mark.asyncio
async def test_me_without_token_401(client, admin):
    resp = await client.get("/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_can_list_users(client, admin):
    token = (await _login(client, "admin@test.com", "admin-pw-123")).json()["access_token"]
    resp = await client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert any(u["email"] == "admin@test.com" for u in resp.json())


@pytest.mark.asyncio
async def test_user_forbidden_on_admin_route_403(client, member):
    token = (await _login(client, "user@test.com", "user-pw-123")).json()["access_token"]
    resp = await client.get("/users", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_creates_user(client, admin):
    token = (await _login(client, "admin@test.com", "admin-pw-123")).json()["access_token"]
    resp = await client.post(
        "/users",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "email": "new@test.com",
            "password": "new-pw-123",
            "display_name": "新人",
            "role": "user",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "new@test.com"
    assert resp.json()["role"] == "user"
