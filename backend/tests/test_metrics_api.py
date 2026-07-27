"""Review metrics API tests: ingest plus legacy overview compatibility."""

from datetime import UTC, date, datetime

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
    for stat_date, play, completion_rate in [
        ("2026-06-27", 10000, 0.31),
        ("2026-06-28", 15000, 0.36),
    ]:
        response = await client.post(
            "/metrics/ingest",
            headers=_auth(token),
            json={
                "stat_date": stat_date,
                "title": "Test content",
                "play": play,
                "exposure": play * 3,
                "completion_rate": completion_rate,
                "like_rate": 0.08,
                "follower_delta": 120,
            },
        )
        assert response.status_code == 201

    overview = await client.get("/metrics/overview?days=365", headers=_auth(token))
    body = overview.json()
    assert body["has_data"] is True
    assert len(body["trend"]) == 2
    assert body["total_play"] == 25000
    assert body["follower_delta"] == 240
    assert abs(body["avg_completion_rate"] - 0.335) < 1e-6


@pytest.mark.asyncio
async def test_ingest_idempotent_by_content_and_date(client, admin, session):
    from app.models import ContentItem, Project

    project = Project(org_id=admin.org_id, name="P")
    session.add(project)
    await session.flush()
    content = ContentItem(project_id=project.id, title="Content A")
    session.add(content)
    await session.commit()
    await session.refresh(content)

    token = await _token(client, "admin@test.com", "admin-pw-123")
    payload = {"content_item_id": content.id, "stat_date": "2026-06-28", "play": 5000}
    first = await client.post("/metrics/ingest", headers=_auth(token), json=payload)
    second = await client.post(
        "/metrics/ingest",
        headers=_auth(token),
        json={**payload, "play": 8000},
    )
    assert first.json()["id"] == second.json()["id"]

    overview = await client.get("/metrics/overview?days=365", headers=_auth(token))
    assert overview.json()["total_play"] == 8000


@pytest.mark.asyncio
async def test_overview_remains_legacy_metric_snapshot_aggregation_for_manual_and_import_rows(
    client, admin, session
):
    from app.models import Account, DataImportBatch, MetricSnapshot, Project
    from app.models.enums import DataSourceKind, ImportBatchStatus, MetricSource, Platform

    project = Project(org_id=admin.org_id, name="Metrics overview boundary")
    session.add(project)
    await session.flush()
    account = Account(
        org_id=admin.org_id,
        project_id=project.id,
        platform=Platform.DOUYIN,
        nickname="Overview account",
    )
    session.add(account)
    await session.flush()
    batch = DataImportBatch(
        org_id=admin.org_id,
        account_id=account.id,
        created_by_id=admin.id,
        source_kind=DataSourceKind.PLATFORM_EXPORT,
        status=ImportBatchStatus.COMMITTED,
        template_code="douyin_work_list_v1",
        content_sha256="a" * 64,
        period_start=date(2026, 7, 20),
        period_end=date(2026, 7, 21),
        committed_at=datetime(2026, 7, 21, 10, 0, tzinfo=UTC),
    )
    session.add(batch)
    await session.flush()
    session.add_all(
        [
            MetricSnapshot(
                org_id=admin.org_id,
                account_id=account.id,
                source=MetricSource.MANUAL,
                stat_date=date(2026, 7, 20),
                title="Manual row",
                play=120,
                exposure=360,
                completion_rate=0.25,
                like_rate=0.04,
                comment_rate=0.01,
                share_rate=0.01,
                follower_delta=2,
            ),
            MetricSnapshot(
                org_id=admin.org_id,
                account_id=account.id,
                import_batch_id=batch.id,
                source=MetricSource.DOUYIN,
                stat_date=date(2026, 7, 21),
                title="Imported row",
                play=300,
                exposure=900,
                completion_rate=0.5,
                like_rate=0.1,
                comment_rate=0.03,
                share_rate=0.02,
                follower_delta=4,
            ),
        ]
    )
    await session.commit()

    token = await _token(client, "admin@test.com", "admin-pw-123")
    response = await client.get("/metrics/overview?days=365", headers=_auth(token))

    assert response.status_code == 200
    assert response.json() == {
        "has_data": True,
        "trend": [
            {"date": "07/20", "play": 120, "exposure": 360},
            {"date": "07/21", "play": 300, "exposure": 900},
        ],
        "engagement": [
            {"date": "07/20", "completion_rate": 0.25, "like_rate": 0.04},
            {"date": "07/21", "completion_rate": 0.5, "like_rate": 0.1},
        ],
        "rank_top": [
            {"title": "Imported row", "completion_rate": 0.5},
            {"title": "Manual row", "completion_rate": 0.25},
        ],
        "rank_bottom": [],
        "total_play": 420,
        "avg_completion_rate": 0.375,
        "follower_delta": 6,
    }
