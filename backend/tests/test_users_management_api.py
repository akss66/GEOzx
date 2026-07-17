"""User management lifecycle and workspace access tests."""

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import (
    Client,
    ClientMembership,
    Org,
    Project,
    ProjectMembership,
    User,
)
from app.models.enums import UserRole, WorkspaceRole


async def _login(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _workspace(session, org_id: int):
    first_client = Client(org_id=org_id, name="品牌甲")
    second_client = Client(org_id=org_id, name="品牌乙")
    session.add_all([first_client, second_client])
    await session.flush()
    first_project = Project(org_id=org_id, client_id=first_client.id, name="新品发布")
    second_project = Project(org_id=org_id, client_id=second_client.id, name="日常运营")
    session.add_all([first_project, second_project])
    await session.commit()
    return first_client, second_client, first_project, second_project


@pytest.mark.asyncio
async def test_admin_reads_user_detail_and_access_catalog(client, session, admin, member):
    first_client, second_client, first_project, second_project = await _workspace(
        session, admin.org_id
    )
    session.add_all(
        [
            ClientMembership(
                client_id=first_client.id,
                user_id=member.id,
                role=WorkspaceRole.OPERATOR,
            ),
            ProjectMembership(
                project_id=first_project.id,
                user_id=member.id,
                role=WorkspaceRole.REVIEWER,
            ),
        ]
    )
    await session.commit()
    token = await _login(client, admin.email, "admin-pw-123")

    detail = await client.get(f"/users/{member.id}", headers=_auth(token))
    catalog = await client.get("/users/access-catalog", headers=_auth(token))

    assert detail.status_code == 200
    assert detail.json()["has_global_access"] is False
    assert detail.json()["client_memberships"] == [
        {"client_id": first_client.id, "client_name": "品牌甲", "role": "operator"}
    ]
    assert detail.json()["project_memberships"] == [
        {
            "project_id": first_project.id,
            "project_name": "新品发布",
            "client_id": first_client.id,
            "client_name": "品牌甲",
            "role": "reviewer",
        }
    ]
    assert catalog.status_code == 200
    assert {item["id"] for item in catalog.json()["clients"]} == {
        first_client.id,
        second_client.id,
    }
    assert {item["id"] for item in catalog.json()["projects"]} == {
        first_project.id,
        second_project.id,
    }


@pytest.mark.asyncio
async def test_admin_atomically_replaces_user_workspace_access(client, session, admin, member):
    first_client, second_client, first_project, second_project = await _workspace(
        session, admin.org_id
    )
    session.add(
        ClientMembership(
            client_id=first_client.id,
            user_id=member.id,
            role=WorkspaceRole.OPERATOR,
        )
    )
    await session.commit()
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.put(
        f"/users/{member.id}/access",
        headers=_auth(token),
        json={
            "clients": [{"client_id": second_client.id, "role": "lead"}],
            "projects": [
                {"project_id": first_project.id, "role": "editor"},
                {"project_id": second_project.id, "role": "reviewer"},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["client_memberships"][0]["client_id"] == second_client.id
    assert {item["role"] for item in body["project_memberships"]} == {
        "editor",
        "reviewer",
    }
    stored_clients = list(
        await session.scalars(
            select(ClientMembership).where(ClientMembership.user_id == member.id)
        )
    )
    stored_projects = list(
        await session.scalars(
            select(ProjectMembership).where(ProjectMembership.user_id == member.id)
        )
    )
    assert [(item.client_id, item.role) for item in stored_clients] == [
        (second_client.id, WorkspaceRole.LEAD)
    ]
    assert len(stored_projects) == 2


@pytest.mark.asyncio
async def test_cross_org_access_assignment_is_rejected_without_partial_write(
    client, session, admin, member
):
    first_client, _, _, _ = await _workspace(session, admin.org_id)
    other_org = Org(name="其他组织")
    other_client = Client(org=other_org, name="越权客户")
    session.add(other_client)
    await session.commit()
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.put(
        f"/users/{member.id}/access",
        headers=_auth(token),
        json={
            "clients": [
                {"client_id": first_client.id, "role": "operator"},
                {"client_id": other_client.id, "role": "lead"},
            ],
            "projects": [],
        },
    )

    assert response.status_code == 404
    stored = list(
        await session.scalars(
            select(ClientMembership).where(ClientMembership.user_id == member.id)
        )
    )
    assert stored == []


@pytest.mark.asyncio
async def test_admin_updates_and_deactivates_another_user(client, admin, member):
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.patch(
        f"/users/{member.id}",
        headers=_auth(token),
        json={
            "email": "operator@test.com",
            "display_name": "运营同事",
            "is_active": False,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": member.id,
        "email": "operator@test.com",
        "display_name": "运营同事",
        "role": "user",
        "is_active": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"is_active": False},
        {"role": "user"},
    ],
)
async def test_admin_cannot_remove_own_admin_access(client, admin, payload):
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.patch(
        f"/users/{admin.id}", headers=_auth(token), json=payload
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_inactive_admin_profile_can_still_be_edited(client, session, admin):
    inactive_admin = User(
        org_id=admin.org_id,
        email="former-admin@test.com",
        hashed_password=hash_password("former-admin-pw"),
        display_name="原管理员",
        role=UserRole.ADMIN,
        is_active=False,
    )
    session.add(inactive_admin)
    await session.commit()
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.patch(
        f"/users/{inactive_admin.id}",
        headers=_auth(token),
        json={"display_name": "已离任管理员"},
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "已离任管理员"


@pytest.mark.asyncio
async def test_member_cannot_manage_user_access(client, admin, member):
    token = await _login(client, member.email, "user-pw-123")

    detail = await client.get(f"/users/{admin.id}", headers=_auth(token))
    update = await client.patch(
        f"/users/{admin.id}", headers=_auth(token), json={"display_name": "越权"}
    )
    access = await client.put(
        f"/users/{member.id}/access",
        headers=_auth(token),
        json={"clients": [], "projects": []},
    )

    assert detail.status_code == 403
    assert update.status_code == 403
    assert access.status_code == 403
