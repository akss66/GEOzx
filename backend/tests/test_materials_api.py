import pytest

from app.models import ContentItem, MaterialAsset, Org, Project, ProjectMembership
from app.models.enums import MaterialStatus, WorkspaceRole


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


@pytest.mark.asyncio
async def test_material_access_follows_project_membership(client, admin, member, session):
    visible_project = Project(org_id=admin.org_id, name="成员可见项目")
    hidden_project = Project(org_id=admin.org_id, name="成员隐藏项目")
    visible_content = ContentItem(project=visible_project, title="可见内容")
    hidden_content = ContentItem(project=hidden_project, title="隐藏内容")
    session.add_all([visible_project, hidden_project, visible_content, hidden_content])
    await session.flush()
    visible = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=visible_content.id,
        kind="video",
        status=MaterialStatus.READY,
        local_path="outputs/visible.mp4",
    )
    hidden = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=hidden_content.id,
        kind="video",
        status=MaterialStatus.READY,
        local_path="outputs/hidden.mp4",
    )
    session.add_all(
        [
            visible,
            hidden,
            ProjectMembership(
                project_id=visible_project.id,
                user_id=member.id,
                role=WorkspaceRole.EDITOR,
            ),
        ]
    )
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")

    listing = await client.get("/materials", headers=_auth(token))
    hidden_meta = await client.get(f"/materials/{hidden.id}", headers=_auth(token))
    hidden_file = await client.get(f"/materials/{hidden.id}/file", headers=_auth(token))
    anonymous_file = await client.get(f"/materials/{visible.id}/file")

    assert [row["id"] for row in listing.json()] == [visible.id]
    assert hidden_meta.status_code == 404
    assert hidden_file.status_code == 404
    assert anonymous_file.status_code == 401
