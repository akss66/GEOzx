"""模型配置接口测试：list 任意用户、改限 admin（async，SQLite override）。"""

import pytest

from app.models import ModelConfig


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_and_update_model_config(client, admin, session):
    cfg = ModelConfig(
        org_id=admin.org_id, agent_code="01-positioning", primary_model="deepseek-chat"
    )
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)

    token = await _token(client, "admin@test.com", "admin-pw-123")
    listing = await client.get("/model-configs", headers=_auth(token))
    assert listing.status_code == 200
    assert any(c["agent_code"] == "01-positioning" for c in listing.json())

    upd = await client.patch(
        f"/model-configs/{cfg.id}",
        headers=_auth(token),
        json={"primary_model": "deepseek-reasoner", "fallback_model": "deepseek-chat"},
    )
    assert upd.status_code == 200
    assert upd.json()["primary_model"] == "deepseek-reasoner"
    assert upd.json()["fallback_model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_user_can_list_but_not_update_model_config(client, member, admin, session):
    cfg = ModelConfig(org_id=admin.org_id, agent_code="02-content", primary_model="deepseek-chat")
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)

    token = await _token(client, "user@test.com", "user-pw-123")
    assert (await client.get("/model-configs", headers=_auth(token))).status_code == 200
    resp = await client.patch(
        f"/model-configs/{cfg.id}", headers=_auth(token), json={"primary_model": "x"}
    )
    assert resp.status_code == 403
