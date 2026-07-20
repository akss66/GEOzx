from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Account, BrainTask, Event, MaterialAsset, MatrixDistributionPlan
from app.models.enums import MaterialStatus


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _account(
    client,
    headers: dict[str, str],
    session,
    nickname: str,
    *,
    authorized: bool,
) -> int:
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": nickname, "platform": "douyin", "external_account_id": nickname},
        )
    ).json()
    if authorized:
        row = await session.get(Account, account["id"])
        assert row is not None
        row.auth = {
            "integration_status": "connected",
            "auth_status": "authorized",
            "data_sync_status": "pending",
        }
        await session.commit()
    return account["id"]


@pytest.mark.asyncio
async def test_matrix_distribution_plan_requires_accounts(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    resp = await client.post(
        "/matrix-distribution-plans",
        headers=headers,
        json={
            "platforms": ["douyin"],
            "account_ids": [],
            "material_ids": [1],
            "title": "矩阵分发",
        },
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_matrix_distribution_plan_rejects_unauthorized_accounts(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _account(client, headers, session, "未授权账号", authorized=False)

    project = (await client.post("/projects", headers=headers, json={"name": "矩阵"})).json()
    content = (
        await client.post(
            "/content-items",
            headers=headers,
            json={"project_id": project["id"], "title": "矩阵内容"},
        )
    ).json()
    material = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=content["id"],
        kind="video",
        status=MaterialStatus.READY,
        local_path="outputs/matrix.mp4",
        size_bytes=1024,
    )
    session.add(material)
    await session.commit()
    await session.refresh(material)

    resp = await client.post(
        "/matrix-distribution-plans",
        headers=headers,
        json={
            "platforms": ["douyin"],
            "account_ids": [account_id],
            "content_item_id": content["id"],
            "material_ids": [material.id],
            "title": "矩阵内容",
        },
    )

    assert resp.status_code == 400
    assert "授权" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_matrix_distribution_plan_creates_publish_packages_and_tool_calls(
    client, admin, session
):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_a = await _account(client, headers, session, "A 账号", authorized=True)
    account_b = await _account(client, headers, session, "B 账号", authorized=True)

    project = (await client.post("/projects", headers=headers, json={"name": "矩阵"})).json()
    for account_id in (account_a, account_b):
        bound = await client.patch(
            f"/accounts/{account_id}",
            headers=headers,
            json={"project_id": project["id"]},
        )
        assert bound.status_code == 200
    content = (
        await client.post(
            "/content-items",
            headers=headers,
            json={"project_id": project["id"], "title": "矩阵内容", "account_id": account_a},
        )
    ).json()
    material = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=content["id"],
        kind="video",
        status=MaterialStatus.READY,
        local_path="outputs/matrix.mp4",
        size_bytes=1024,
    )
    session.add(material)
    await session.commit()
    await session.refresh(material)

    scheduled_at = datetime.now(UTC) + timedelta(hours=3)
    resp = await client.post(
        "/matrix-distribution-plans",
        headers=headers,
        json={
            "platforms": ["douyin"],
            "account_ids": [account_a, account_b],
            "content_item_id": content["id"],
            "material_ids": [material.id],
            "title": "矩阵内容",
            "body": "一条素材，多账号执行。",
            "topics": ["agent", "matrix"],
            "scheduled_at": scheduled_at.isoformat(),
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending_approval"
    assert body["platforms"] == ["douyin"]
    assert body["account_ids"] == [account_a, account_b]
    assert len(body["items"]) == 2
    assert {item["account_id"] for item in body["items"]} == {account_a, account_b}
    assert all(item["status"] == "waiting_manual" for item in body["items"])
    assert all(item["tool_call_id"] for item in body["items"])
    assert all(
        item["publish_package"]["execution_mode"] == "manual_checklist" for item in body["items"]
    )
    assert all(item["publish_package"]["manual_steps"] for item in body["items"])
    stored_plan = await session.get(MatrixDistributionPlan, body["id"])
    stored_task = await session.scalar(
        select(BrainTask).where(BrainTask.title == "Matrix distribution: 矩阵内容")
    )
    assert stored_plan is not None and stored_plan.created_by_id == admin.id
    assert stored_task is not None and stored_task.created_by_id == admin.id

    queue = await client.get("/brain/tool-calls/pending-approvals", headers=headers)
    package_calls = [row for row in queue.json() if row["tool_code"] == "publish_package_prepare"]
    assert len(package_calls) >= 2
    assert {row["meta"]["matrix_plan_id"] for row in package_calls} == {body["id"]}
    approval_events = list(
        await session.scalars(
            select(Event).where(
                Event.type == "approval.requested",
                Event.content_item_id == content["id"],
            )
        )
    )
    matrix_events = [
        row for row in approval_events if row.payload.get("approval_kind") == "matrix_plan"
    ]
    assert len(matrix_events) == 1
    assert matrix_events[0].payload["source_id"] == body["id"]


@pytest.mark.asyncio
async def test_approving_all_matrix_publish_packages_queues_plan(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account_id = await _account(client, headers, session, "A 账号", authorized=True)

    project = (await client.post("/projects", headers=headers, json={"name": "矩阵"})).json()
    bound = await client.patch(
        f"/accounts/{account_id}",
        headers=headers,
        json={"project_id": project["id"]},
    )
    assert bound.status_code == 200
    content = (
        await client.post(
            "/content-items",
            headers=headers,
            json={"project_id": project["id"], "title": "矩阵内容", "account_id": account_id},
        )
    ).json()
    material = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=content["id"],
        kind="video",
        status=MaterialStatus.READY,
        local_path="outputs/matrix.mp4",
        size_bytes=1024,
    )
    session.add(material)
    await session.commit()
    await session.refresh(material)

    created = await client.post(
        "/matrix-distribution-plans",
        headers=headers,
        json={
            "platforms": ["douyin"],
            "account_ids": [account_id],
            "content_item_id": content["id"],
            "material_ids": [material.id],
            "title": "矩阵内容",
        },
    )
    plan = created.json()
    tool_call_id = plan["items"][0]["tool_call_id"]

    approved = await client.post(
        f"/brain/tool-calls/{tool_call_id}/approve",
        headers=headers,
        json={"approved": True, "comment": "可以人工发布"},
    )
    listed = await client.get("/matrix-distribution-plans", headers=headers)

    assert approved.status_code == 200
    assert approved.json()["meta"]["publish_decision_status"] == "approved_for_manual_publish"
    next_plan = next(row for row in listed.json() if row["id"] == plan["id"])
    assert next_plan["status"] == "queued"
    assert next_plan["items"][0]["status"] == "queued"
