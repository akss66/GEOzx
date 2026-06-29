"""复盘看板接口测试：指标录入（幂等）+ 聚合视图（async，SQLite override）。"""

import pytest


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_overview_empty(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    resp = await client.get("/metrics/overview", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_data"] is False
    assert body["trend"] == []
    assert body["total_play"] == 0


@pytest.mark.asyncio
async def test_ingest_and_overview(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    # 录入两天数据
    for d, play, comp in [("2026-06-27", 10000, 0.31), ("2026-06-28", 15000, 0.36)]:
        r = await client.post(
            "/metrics/ingest",
            headers=_auth(token),
            json={
                "stat_date": d,
                "title": "测试内容",
                "play": play,
                "exposure": play * 3,
                "completion_rate": comp,
                "like_rate": 0.08,
                "follower_delta": 120,
            },
        )
        assert r.status_code == 201

    ov = await client.get("/metrics/overview?days=365", headers=_auth(token))
    body = ov.json()
    assert body["has_data"] is True
    assert len(body["trend"]) == 2
    assert body["total_play"] == 25000
    assert body["follower_delta"] == 240
    # 完播率平均 (0.31+0.36)/2
    assert abs(body["avg_completion_rate"] - 0.335) < 1e-6


@pytest.mark.asyncio
async def test_ingest_idempotent_by_content_and_date(client, admin, session):
    from app.models import ContentItem, Project

    project = Project(org_id=admin.org_id, name="P")
    session.add(project)
    await session.flush()
    ci = ContentItem(project_id=project.id, title="内容A")
    session.add(ci)
    await session.commit()
    await session.refresh(ci)

    token = await _token(client, "admin@test.com", "admin-pw-123")
    payload = {"content_item_id": ci.id, "stat_date": "2026-06-28", "play": 5000}
    r1 = await client.post("/metrics/ingest", headers=_auth(token), json=payload)
    # 同 content+date 再回流，更新而非新增
    r2 = await client.post(
        "/metrics/ingest",
        headers=_auth(token),
        json={**payload, "play": 8000},
    )
    assert r1.json()["id"] == r2.json()["id"]

    ov = await client.get("/metrics/overview?days=365", headers=_auth(token))
    assert ov.json()["total_play"] == 8000  # 更新后的值，非 5000+8000
