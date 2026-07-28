import pytest

from app.models import (
    Account,
    AccountMembership,
    Client,
    ClientMembership,
    ContentItem,
    MaterialAsset,
    Org,
    Project,
    ProjectMembership,
)
from app.models.enums import AccountStatus, MaterialStatus, Platform, WorkspaceRole


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


@pytest.mark.asyncio
async def test_account_scoped_content_detail_and_material_access_require_its_account(
    client, admin, member, session
):
    workspace = Client(org_id=admin.org_id, name="Account-scoped workspace")
    visible_account = Account(
        org_id=admin.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="Visible account",
        status=AccountStatus.ACTIVE,
    )
    hidden_account = Account(
        org_id=admin.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="Hidden account",
    )
    member.account_scope_mode = "selected"
    session.add_all(
        [
            workspace,
            visible_account,
            hidden_account,
            ClientMembership(
                client=workspace,
                user=member,
                role=WorkspaceRole.OPERATOR,
            ),
            AccountMembership(user=member, account=visible_account),
        ]
    )
    await session.flush()

    visible_content = ContentItem(
        account_id=visible_account.id,
        project_id=None,
        title="Visible account diagnostic",
    )
    hidden_content = ContentItem(
        account_id=hidden_account.id,
        project_id=None,
        title="Hidden account diagnostic",
    )
    session.add_all([visible_content, hidden_content])
    await session.flush()
    visible_material = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=visible_content.id,
        kind="image",
        status=MaterialStatus.READY,
        local_path="outputs/visible-account.png",
    )
    hidden_material = MaterialAsset(
        org_id=admin.org_id,
        content_item_id=hidden_content.id,
        kind="image",
        status=MaterialStatus.READY,
        local_path="outputs/hidden-account.png",
    )
    session.add_all([visible_material, hidden_material])
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")
    headers = _auth(token)

    visible_detail = await client.get(f"/content-items/{visible_content.id}", headers=headers)
    visible_listing = await client.get(
        f"/materials?content_item_id={visible_content.id}", headers=headers
    )
    visible_all_listing = await client.get("/materials", headers=headers)
    visible_material_response = await client.get(
        f"/materials/{visible_material.id}", headers=headers
    )
    hidden_detail = await client.get(f"/content-items/{hidden_content.id}", headers=headers)
    hidden_listing = await client.get(
        f"/materials?content_item_id={hidden_content.id}", headers=headers
    )
    hidden_material_response = await client.get(f"/materials/{hidden_material.id}", headers=headers)

    assert visible_detail.status_code == 200
    assert visible_listing.status_code == 200
    assert [row["id"] for row in visible_listing.json()] == [visible_material.id]
    assert [row["id"] for row in visible_all_listing.json()] == [visible_material.id]
    assert visible_material_response.status_code == 200
    assert hidden_detail.status_code == 404
    assert hidden_listing.status_code == 404
    assert hidden_material_response.status_code == 404


@pytest.mark.asyncio
async def test_account_scoped_content_rejects_inactive_or_unbound_accounts(client, admin, session):
    inactive_account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Inactive account",
        status=AccountStatus.INACTIVE,
    )
    session.add(inactive_account)
    await session.flush()
    inactive_content = ContentItem(
        account_id=inactive_account.id,
        project_id=None,
        title="Inactive account diagnostic",
    )
    unbound_content = ContentItem(
        account_id=None,
        project_id=None,
        title="Invalid unbound diagnostic",
    )
    session.add_all([inactive_content, unbound_content])
    await session.commit()
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)

    inactive_response = await client.get(f"/content-items/{inactive_content.id}", headers=headers)
    unbound_response = await client.get(f"/content-items/{unbound_content.id}", headers=headers)

    assert inactive_response.status_code == 409
    assert unbound_response.status_code == 404
