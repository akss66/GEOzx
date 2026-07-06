import pytest

from app.models import ContentItem, MaterialAsset, Org, Project
from app.models.enums import MaterialStatus


async def _token(client, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_materials_filters_by_content_item_and_org(client, admin, session):
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    project = Project(org_id=admin.org_id, name="Project")
    content = ContentItem(project=project, title="Content")
    other_org = Org(name="Other")
    other_project = Project(org=other_org, name="Other project")
    other_content = ContentItem(project=other_project, title="Other content")
    session.add_all([project, content, other_org, other_project, other_content])
    await session.flush()

    visible = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=content.id,
        kind="video",
        provider="fake",
        status=MaterialStatus.READY,
        local_path="outputs/content.mp4",
        size_bytes=100,
    )
    hidden_same_org = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=None,
        kind="image",
        status=MaterialStatus.READY,
        local_path="outputs/cover.png",
    )
    hidden_other_org = MaterialAsset(
        org_id=other_org.id,
        content_item_id=other_content.id,
        kind="video",
        status=MaterialStatus.READY,
        local_path="outputs/other.mp4",
    )
    session.add_all([visible, hidden_same_org, hidden_other_org])
    await session.commit()

    resp = await client.get(f"/materials?content_item_id={content.id}", headers=headers)

    assert resp.status_code == 200
    rows = resp.json()
    assert [row["id"] for row in rows] == [visible.id]
    assert rows[0]["file_url"] == f"/materials/{visible.id}/file"
    assert rows[0]["status"] == "ready"
    assert rows[0]["content_item_id"] == content.id
