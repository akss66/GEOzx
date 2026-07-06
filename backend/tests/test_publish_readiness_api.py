from datetime import datetime, timedelta, timezone

import pytest

from app.models import MaterialAsset
from app.models.enums import MaterialStatus


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_publish_capabilities_include_safe_execution_modes(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    resp = await client.get("/publish-capabilities", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert [row["platform"] for row in body] == ["douyin", "xiaohongshu", "shipinhao"]
    douyin = next(row for row in body if row["platform"] == "douyin")
    assert "video" in douyin["content_types"]
    assert "image_text" in douyin["content_types"]
    assert douyin["execution_mode"] == "manual_checklist"
    assert douyin["permission_status"] == "prepare_only"
    assert douyin["browser_runner_enabled"] is False
    assert "scheduled_at" in douyin["supported_fields"]


@pytest.mark.asyncio
async def test_publish_readiness_creates_human_confirmation_tool_call(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    project = (await client.post("/projects", headers=headers, json={"name": "Douyin"})).json()
    content = (
        await client.post(
            "/content-items",
            headers=headers,
            json={"project_id": project["id"], "title": "New launch video"},
        )
    ).json()
    material = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=content["id"],
        kind="video",
        status=MaterialStatus.READY,
        local_path="outputs/new-launch.mp4",
        size_bytes=1024,
    )
    cover = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=content["id"],
        kind="image",
        status=MaterialStatus.READY,
        local_path="outputs/new-launch-cover.jpg",
        size_bytes=256,
    )
    session.add_all([material, cover])
    await session.commit()
    await session.refresh(material)
    await session.refresh(cover)

    scheduled_at = datetime.now(timezone.utc) + timedelta(hours=3)
    resp = await client.post(
        f"/content-items/{content['id']}/publish-readiness",
        headers=headers,
        json={
            "platform": "douyin",
            "title": "New launch video",
            "body": "One sentence, then the execution plan.",
            "topics": ["launch", "agent"],
            "scheduled_at": scheduled_at.isoformat(),
            "material_ids": [material.id],
            "cover_material_id": cover.id,
            "visibility": "public",
            "allow_comment": True,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["content_item_id"] == content["id"]
    assert body["platform"] == "douyin"
    assert body["risk"] == "pass"
    assert body["ready"] is True
    assert body["tool_call"]["tool_code"] == "publish_package_prepare"
    assert body["tool_call"]["status"] == "waiting_approval"
    assert body["tool_call"]["requires_human_confirmation"] is True
    assert body["package"]["account_id"] is None
    assert body["package"]["content_type"] == "video"
    assert body["package"]["execution_mode"] == "manual_checklist"
    assert body["package"]["manual_steps"][0].startswith("打开抖音创作者")
    assert body["package"]["cover_material_id"] == cover.id
    assert body["package"]["visibility"] == "public"
    assert body["package"]["allow_comment"] is True
    assert body["tool_call"]["meta"]["material_ids"] == [material.id]
    assert body["tool_call"]["meta"]["publish_package"]["cover_material_id"] == cover.id
    assert body["tool_call"]["meta"]["publish_package"]["visibility"] == "public"
    assert body["tool_call"]["meta"]["publish_package"]["execution_mode"] == "manual_checklist"
    assert body["tool_call"]["meta"]["publish_package"]["manual_steps"]

    queue = await client.get("/brain/tool-calls/pending-approvals", headers=headers)
    assert any(
        row["id"] == body["tool_call"]["id"] and row["module"] == "content_production"
        for row in queue.json()
    )


@pytest.mark.asyncio
async def test_publish_readiness_blocks_missing_material(client, admin):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    project = (await client.post("/projects", headers=headers, json={"name": "Douyin"})).json()
    content = (
        await client.post(
            "/content-items",
            headers=headers,
            json={"project_id": project["id"], "title": "Missing material"},
        )
    ).json()

    resp = await client.post(
        f"/content-items/{content['id']}/publish-readiness",
        headers=headers,
        json={
            "platform": "douyin",
            "title": "Missing material",
            "body": "",
            "topics": [],
            "material_ids": [],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["risk"] == "block"
    assert body["ready"] is False
    assert body["tool_call"]["status"] == "failed"
    assert any(finding["code"] == "material.required" for finding in body["findings"])


@pytest.mark.asyncio
async def test_publish_readiness_blocks_missing_cover_material(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    project = (await client.post("/projects", headers=headers, json={"name": "Douyin"})).json()
    content = (
        await client.post(
            "/content-items",
            headers=headers,
            json={"project_id": project["id"], "title": "Cover check"},
        )
    ).json()
    material = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=content["id"],
        kind="video",
        status=MaterialStatus.READY,
        local_path="outputs/cover-check.mp4",
        size_bytes=1024,
    )
    session.add(material)
    await session.commit()
    await session.refresh(material)

    resp = await client.post(
        f"/content-items/{content['id']}/publish-readiness",
        headers=headers,
        json={
            "platform": "douyin",
            "title": "Cover check",
            "material_ids": [material.id],
            "cover_material_id": 999999,
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["risk"] == "block"
    assert body["ready"] is False
    assert any(finding["code"] == "cover.missing" for finding in body["findings"])


@pytest.mark.asyncio
async def test_approving_publish_package_marks_manual_publish_decision(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    project = (await client.post("/projects", headers=headers, json={"name": "Douyin"})).json()
    content = (
        await client.post(
            "/content-items",
            headers=headers,
            json={"project_id": project["id"], "title": "Manual publish package"},
        )
    ).json()
    material = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=content["id"],
        kind="video",
        status=MaterialStatus.READY,
        local_path="outputs/manual-package.mp4",
        size_bytes=1024,
    )
    session.add(material)
    await session.commit()
    await session.refresh(material)

    readiness = await client.post(
        f"/content-items/{content['id']}/publish-readiness",
        headers=headers,
        json={
            "platform": "douyin",
            "title": "Manual publish package",
            "material_ids": [material.id],
        },
    )
    tool_call_id = readiness.json()["tool_call"]["id"]

    approved = await client.post(
        f"/brain/tool-calls/{tool_call_id}/approve",
        headers=headers,
        json={"approved": True, "comment": "确认人工发布"},
    )

    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "success"
    assert body["meta"]["publish_decision_status"] == "approved_for_manual_publish"
    assert body["meta"]["decision"]["approved"] is True
