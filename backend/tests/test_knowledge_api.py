"""共享知识库接口测试：CRUD + 按 category 过滤 + org 隔离（async，SQLite override）。"""

import pytest


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_create_list_filter_knowledge(client, member):
    token = await _token(client, "user@test.com", "user-pw-123")

    # 任意登录用户可写（知识库全体可读可写）
    hot = await client.post(
        "/knowledge",
        headers=_auth(token),
        json={
            "category": "hot_content",
            "title": "对比实测类爆款结构",
            "payload": {"structure": ["钩子", "冲突", "反转", "结论"]},
            "tags": ["数码"],
        },
    )
    assert hot.status_code == 201
    assert hot.json()["category"] == "hot_content"

    await client.post(
        "/knowledge",
        headers=_auth(token),
        json={"category": "script_library", "title": "差评安抚话术", "payload": {}},
    )

    # 全部
    all_rows = await client.get("/knowledge", headers=_auth(token))
    assert len(all_rows.json()) == 2

    # 按 category 过滤
    only_hot = await client.get("/knowledge?category=hot_content", headers=_auth(token))
    assert [k["title"] for k in only_hot.json()] == ["对比实测类爆款结构"]


@pytest.mark.asyncio
async def test_update_and_delete_knowledge(client, member):
    token = await _token(client, "user@test.com", "user-pw-123")
    eid = (
        await client.post(
            "/knowledge",
            headers=_auth(token),
            json={"category": "prompt_library", "title": "草稿", "payload": {"v": 1}},
        )
    ).json()["id"]

    upd = await client.patch(
        f"/knowledge/{eid}",
        headers=_auth(token),
        json={"title": "定稿", "payload": {"v": 2}, "tags": ["美术"]},
    )
    assert upd.status_code == 200
    assert upd.json()["title"] == "定稿"
    assert upd.json()["payload"] == {"v": 2}
    assert upd.json()["tags"] == ["美术"]

    assert (await client.delete(f"/knowledge/{eid}", headers=_auth(token))).status_code == 204
    assert (await client.get("/knowledge", headers=_auth(token))).json() == []


@pytest.mark.asyncio
async def test_knowledge_unknown_entry_404(client, member):
    token = await _token(client, "user@test.com", "user-pw-123")
    resp = await client.patch("/knowledge/99999", headers=_auth(token), json={"title": "x"})
    assert resp.status_code == 404
