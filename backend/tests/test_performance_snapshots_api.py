import pytest


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_performance_snapshots_filter_by_account(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "Matrix A", "platform": "douyin", "external_account_id": "a"},
        )
    ).json()

    await client.post(
        "/metrics/ingest",
        headers=headers,
        json={
            "account_id": account["id"],
            "source": "douyin",
            "stat_date": "2026-07-06",
            "title": "Matrix launch",
            "play": 12000,
            "exposure": 30000,
            "completion_rate": 0.42,
            "like_rate": 0.08,
            "comment_rate": 0.02,
            "share_rate": 0.01,
        },
    )
    await client.post(
        "/metrics/ingest",
        headers=headers,
        json={
            "account_id": account["id"] + 999,
            "stat_date": "2026-07-06",
            "title": "Other account",
            "play": 1,
        },
    )

    resp = await client.get(
        f"/metrics/performance-snapshots?account_id={account['id']}",
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["account_id"] == account["id"]
    assert body[0]["source"] == "douyin"
    assert body[0]["title"] == "Matrix launch"
    assert body[0]["play"] == 12000
    assert body[0]["completion_rate"] == 0.42


@pytest.mark.asyncio
async def test_review_metrics_exclude_demo_source(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    await client.post(
        "/metrics/ingest",
        headers=headers,
        json={
            "source": "douyin",
            "stat_date": "2026-07-06",
            "title": "Real douyin content",
            "play": 100,
            "exposure": 300,
            "completion_rate": 0.5,
            "like_rate": 0.1,
            "follower_delta": 3,
        },
    )
    await client.post(
        "/metrics/ingest",
        headers=headers,
        json={
            "source": "demo",
            "stat_date": "2026-07-06",
            "title": "Demo content",
            "play": 9999,
            "exposure": 9999,
            "completion_rate": 0.99,
            "like_rate": 0.99,
            "follower_delta": 99,
        },
    )

    overview = (await client.get("/metrics/overview?days=30", headers=headers)).json()
    snapshots = (await client.get("/metrics/performance-snapshots", headers=headers)).json()

    assert overview["has_data"] is True
    assert overview["total_play"] == 100
    assert overview["follower_delta"] == 3
    assert [row["source"] for row in snapshots] == ["douyin"]
    assert snapshots[0]["title"] == "Real douyin content"
