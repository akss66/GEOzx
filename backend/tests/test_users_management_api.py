"""User management lifecycle and workspace access tests."""

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models import (
    Account,
    AccountClient,
    AccountMembership,
    Client,
    ClientMembership,
    Event,
    Org,
    Project,
    ProjectMembership,
    User,
)
from app.models.enums import Platform, UserRole, WorkspaceRole


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
    catalog_account = Account(
        org_id=admin.org_id,
        client_id=first_client.id,
        project_id=first_project.id,
        platform=Platform.DOUYIN,
        nickname="Catalog account",
    )
    session.add_all(
        [
            catalog_account,
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
    await session.flush()
    session.add(AccountClient(account_id=catalog_account.id, client_id=second_client.id))
    await session.commit()
    token = await _login(client, admin.email, "admin-pw-123")

    detail = await client.get(f"/users/{member.id}", headers=_auth(token))
    catalog = await client.get("/users/access-catalog", headers=_auth(token))

    assert detail.status_code == 200
    assert detail.json()["has_global_access"] is False
    assert detail.json()["account_scope_mode"] == "all_accessible"
    assert detail.json()["account_ids"] == []
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
    assert catalog.json()["accounts"] == [
        {
            "id": catalog_account.id,
            "client_id": first_client.id,
            "client_ids": [first_client.id, second_client.id],
            "project_ids": [first_project.id],
            "nickname": "Catalog account",
            "platform": "douyin",
            "status": "active",
        }
    ]


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
    selected_account = Account(
        org_id=admin.org_id,
        client_id=second_client.id,
        platform=Platform.DOUYIN,
        nickname="Selected account",
    )
    session.add(selected_account)
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
            "account_scope_mode": "selected",
            "account_ids": [selected_account.id],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["client_memberships"][0]["client_id"] == second_client.id
    assert {item["role"] for item in body["project_memberships"]} == {
        "editor",
        "reviewer",
    }
    assert body["account_scope_mode"] == "selected"
    assert body["account_ids"] == [selected_account.id]
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
    stored_accounts = list(
        await session.scalars(
            select(AccountMembership).where(AccountMembership.user_id == member.id)
        )
    )
    assert [(item.client_id, item.role) for item in stored_clients] == [
        (second_client.id, WorkspaceRole.LEAD)
    ]
    assert len(stored_projects) == 2
    assert [item.account_id for item in stored_accounts] == [selected_account.id]


@pytest.mark.asyncio
async def test_selected_accounts_must_be_in_requested_workspace_scope(
    client, session, admin, member
):
    first_client, second_client, _, _ = await _workspace(session, admin.org_id)
    out_of_scope_account = Account(
        org_id=admin.org_id,
        client_id=second_client.id,
        platform=Platform.DOUYIN,
        nickname="Out of scope account",
    )
    session.add(out_of_scope_account)
    await session.commit()
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.put(
        f"/users/{member.id}/access",
        headers=_auth(token),
        json={
            "clients": [{"client_id": first_client.id, "role": "operator"}],
            "projects": [],
            "account_scope_mode": "selected",
            "account_ids": [out_of_scope_account.id],
        },
    )

    assert response.status_code == 404
    assert list(
        await session.scalars(
            select(ClientMembership).where(ClientMembership.user_id == member.id)
        )
    ) == []
    assert list(
        await session.scalars(
            select(AccountMembership).where(AccountMembership.user_id == member.id)
        )
    ) == []


@pytest.mark.asyncio
async def test_cross_org_selected_account_assignment_is_rejected_without_partial_write(
    client, session, admin, member
):
    first_client, _, _, _ = await _workspace(session, admin.org_id)
    other_org = Org(name="Other organization")
    other_account = Account(
        org=other_org,
        platform=Platform.DOUYIN,
        nickname="Cross-org account",
    )
    session.add(other_account)
    await session.commit()
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.put(
        f"/users/{member.id}/access",
        headers=_auth(token),
        json={
            "clients": [{"client_id": first_client.id, "role": "operator"}],
            "projects": [],
            "account_scope_mode": "selected",
            "account_ids": [other_account.id],
        },
    )

    assert response.status_code == 404
    assert list(
        await session.scalars(
            select(ClientMembership).where(ClientMembership.user_id == member.id)
        )
    ) == []
    assert list(
        await session.scalars(
            select(AccountMembership).where(AccountMembership.user_id == member.id)
        )
    ) == []


@pytest.mark.asyncio
async def test_duplicate_selected_account_ids_are_rejected(client, session, admin, member):
    workspace, _, _, _ = await _workspace(session, admin.org_id)
    account = Account(
        org_id=admin.org_id,
        client_id=workspace.id,
        platform=Platform.DOUYIN,
        nickname="Duplicate account",
    )
    session.add(account)
    await session.commit()
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.put(
        f"/users/{member.id}/access",
        headers=_auth(token),
        json={
            "clients": [{"client_id": workspace.id, "role": "operator"}],
            "projects": [],
            "account_scope_mode": "selected",
            "account_ids": [account.id, account.id],
        },
    )

    assert response.status_code == 422


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
async def test_admin_resets_another_users_password_without_exposing_plaintext(
    client, session, admin, member
):
    token = await _login(client, admin.email, "admin-pw-123")
    replacement = "replacement-login-pw-123"

    response = await client.post(
        f"/users/{member.id}/reset-password",
        headers=_auth(token),
        json={"new_password": replacement},
    )

    assert response.status_code == 204
    assert replacement not in response.text
    assert (await client.post(
        "/auth/login", json={"email": member.email, "password": "user-pw-123"}
    )).status_code == 401
    assert (await client.post(
        "/auth/login", json={"email": member.email, "password": replacement}
    )).status_code == 200
    events = list(await session.scalars(select(Event)))
    assert all(replacement not in str(event.payload) for event in events)


@pytest.mark.asyncio
async def test_admin_cannot_reset_own_login_password(client, admin):
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.post(
        f"/users/{admin.id}/reset-password",
        headers=_auth(token),
        json={"new_password": "replacement-login-pw-123"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "USER_SELF_PASSWORD_RESET_FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"is_active": False},
        {"role": "user"},
    ],
)
async def test_admin_cannot_remove_own_admin_access_with_stable_code(
    client, session, admin, payload
):
    backup_admin = User(
        org_id=admin.org_id,
        email="backup-admin@test.com",
        hashed_password=hash_password("backup-admin-pw"),
        display_name="Backup admin",
        role=UserRole.ADMIN,
    )
    session.add(backup_admin)
    await session.commit()
    admin_id = admin.id
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.patch(
        f"/users/{admin.id}", headers=_auth(token), json=payload
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "USER_SELF_ADMIN_CHANGE_FORBIDDEN"
    session.expire_all()
    stored = await session.get(User, admin_id)
    assert stored is not None
    assert stored.role == UserRole.ADMIN
    assert stored.is_active is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"is_active": False},
        {"role": "user"},
    ],
)
async def test_last_active_admin_cannot_lose_access_with_stable_code(
    client, session, admin, payload
):
    admin_id = admin.id
    token = await _login(client, admin.email, "admin-pw-123")

    response = await client.patch(
        f"/users/{admin.id}", headers=_auth(token), json=payload
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "USER_LAST_ACTIVE_ADMIN_REQUIRED"
    session.expire_all()
    stored = await session.get(User, admin_id)
    assert stored is not None
    assert stored.role == UserRole.ADMIN
    assert stored.is_active is True


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
