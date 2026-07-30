import pytest

from app.models import Account, ClientMembership, PlatformAccountAuth
from app.models.enums import Platform, WorkspaceRole


async def _token(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_workspace_context_includes_synchronized_account_avatar(
    client,
    admin,
    session,
) -> None:
    token = await _token(client, "admin@test.com", "admin-pw-123")
    account = (
        await client.post(
            "/accounts",
            headers=_auth(token),
            json={"nickname": "Avatar account", "platform": "douyin"},
        )
    ).json()
    avatar_url = "https://p3.douyinpic.com/aweme/100x100/avatar.jpeg"
    session.add(
        PlatformAccountAuth(
            org_id=admin.org_id,
            account_id=account["id"],
            platform="douyin",
            raw_profile={"avatar": avatar_url},
        )
    )
    await session.commit()

    response = await client.get("/workspace-context", headers=_auth(token))

    assert response.status_code == 200
    assert response.json()["accounts"][0]["avatar_url"] == avatar_url


@pytest.mark.asyncio
async def test_workspace_context_is_permission_filtered(client, admin, member, session) -> None:
    admin_token = await _token(client, "admin@test.com", "admin-pw-123")
    member_token = await _token(client, "user@test.com", "user-pw-123")
    allowed = (
        await client.post("/clients", headers=_auth(admin_token), json={"name": "云帆科技"})
    ).json()
    hidden = (
        await client.post("/clients", headers=_auth(admin_token), json={"name": "其他客户"})
    ).json()
    session.add(
        ClientMembership(
            client_id=allowed["id"], user_id=member.id, role=WorkspaceRole.OPERATOR
        )
    )
    await session.commit()

    response = await client.get("/workspace-context", headers=_auth(member_token))

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["clients"]] == [allowed["id"]]
    assert hidden["id"] not in {row["id"] for row in response.json()["clients"]}


@pytest.mark.asyncio
async def test_assigning_an_account_to_another_project_keeps_both_relations(client, admin) -> None:
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    workspace = (await client.post("/clients", headers=headers, json={"name": "客户"})).json()
    first = (
        await client.post(
            "/projects", headers=headers, json={"name": "项目 A", "client_id": workspace["id"]}
        )
    ).json()
    second = (
        await client.post(
            "/projects", headers=headers, json={"name": "项目 B", "client_id": workspace["id"]}
        )
    ).json()
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={
                "nickname": "阿燊",
                "platform": "douyin",
                "client_id": workspace["id"],
                "project_id": first["id"],
            },
        )
    ).json()

    updated = await client.patch(
        f"/accounts/{account['id']}", headers=headers, json={"project_id": second["id"]}
    )

    assert updated.status_code == 200
    assert updated.json()["project_ids"] == [first["id"], second["id"]]


@pytest.mark.asyncio
async def test_account_can_bind_multiple_clients_and_projects_with_explicit_defaults(
    client,
    admin,
) -> None:
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    first_client = (
        await client.post("/clients", headers=headers, json={"name": "客户 A"})
    ).json()
    second_client = (
        await client.post("/clients", headers=headers, json={"name": "客户 B"})
    ).json()
    first_project = (
        await client.post(
            "/projects",
            headers=headers,
            json={"name": "项目 A", "client_id": first_client["id"]},
        )
    ).json()
    second_project = (
        await client.post(
            "/projects",
            headers=headers,
            json={"name": "项目 B", "client_id": second_client["id"]},
        )
    ).json()
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={
                "nickname": "跨客户矩阵账号",
                "platform": "douyin",
                "client_id": first_client["id"],
                "project_id": first_project["id"],
            },
        )
    ).json()

    response = await client.put(
        f"/accounts/{account['id']}/assignments",
        headers=headers,
        json={
            "client_ids": [first_client["id"], second_client["id"]],
            "project_ids": [first_project["id"], second_project["id"]],
            "default_client_id": second_client["id"],
            "default_project_id": second_project["id"],
        },
    )

    assert response.status_code == 200
    assert response.json()["client_id"] == second_client["id"]
    assert response.json()["client_ids"] == sorted(
        [first_client["id"], second_client["id"]]
    )
    assert response.json()["project_id"] == second_project["id"]
    assert response.json()["project_ids"] == sorted(
        [first_project["id"], second_project["id"]]
    )

    detail = await client.get(f"/accounts/{account['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["client_ids"] == response.json()["client_ids"]

    second_context = await client.get(
        f"/workspace-context?client_id={second_client['id']}",
        headers=headers,
    )
    assert second_context.status_code == 200
    assert [row["id"] for row in second_context.json()["accounts"]] == [account["id"]]


@pytest.mark.asyncio
async def test_unassigned_account_remains_available_in_workspace_context(
    client,
    admin,
) -> None:
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    workspace = (
        await client.post("/clients", headers=headers, json={"name": "客户 A"})
    ).json()
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={"nickname": "独立工作账号", "platform": "douyin"},
        )
    ).json()
    clear_response = await client.put(
        f"/accounts/{account['id']}/assignments",
        headers=headers,
        json={
            "client_ids": [],
            "project_ids": [],
            "default_client_id": None,
            "default_project_id": None,
        },
    )
    assert clear_response.status_code == 200

    response = await client.get(
        f"/workspace-context?client_id={workspace['id']}",
        headers=headers,
    )

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["accounts"]] == [account["id"]]
    assert response.json()["accounts"][0]["client_ids"] == []
    assert response.json()["accounts"][0]["project_ids"] == []


@pytest.mark.asyncio
async def test_workspace_context_supports_unassigned_accounts_without_any_clients(
    client,
    admin,
    session,
) -> None:
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    account = Account(
        org_id=admin.org_id,
        nickname="无客户独立账号",
        platform=Platform.DOUYIN,
    )
    session.add(account)
    await session.commit()

    response = await client.get("/workspace-context", headers=headers)

    assert response.status_code == 200
    assert response.json()["clients"] == []
    assert response.json()["selected_client"] is None
    assert response.json()["projects"] == []
    assert [row["id"] for row in response.json()["accounts"]] == [account.id]


@pytest.mark.asyncio
async def test_account_assignments_can_be_cleared_for_independent_work(
    client,
    admin,
) -> None:
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    workspace = (
        await client.post("/clients", headers=headers, json={"name": "客户 A"})
    ).json()
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={
                "nickname": "可解除归属账号",
                "platform": "douyin",
                "client_id": workspace["id"],
            },
        )
    ).json()

    response = await client.put(
        f"/accounts/{account['id']}/assignments",
        headers=headers,
        json={
            "client_ids": [],
            "project_ids": [],
            "default_client_id": None,
            "default_project_id": None,
        },
    )

    assert response.status_code == 200
    assert response.json()["client_id"] is None
    assert response.json()["client_ids"] == []
    assert response.json()["project_id"] is None
    assert response.json()["project_ids"] == []


@pytest.mark.asyncio
async def test_account_assignment_rejects_project_outside_selected_clients(
    client,
    admin,
) -> None:
    token = await _token(client, "admin@test.com", "admin-pw-123")
    headers = _auth(token)
    first_client = (
        await client.post("/clients", headers=headers, json={"name": "客户 A"})
    ).json()
    second_client = (
        await client.post("/clients", headers=headers, json={"name": "客户 B"})
    ).json()
    foreign_project = (
        await client.post(
            "/projects",
            headers=headers,
            json={"name": "客户 B 项目", "client_id": second_client["id"]},
        )
    ).json()
    account = (
        await client.post(
            "/accounts",
            headers=headers,
            json={
                "nickname": "客户 A 账号",
                "platform": "douyin",
                "client_id": first_client["id"],
            },
        )
    ).json()

    response = await client.put(
        f"/accounts/{account['id']}/assignments",
        headers=headers,
        json={
            "client_ids": [first_client["id"]],
            "project_ids": [foreign_project["id"]],
            "default_client_id": first_client["id"],
            "default_project_id": None,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "所选项目必须属于已绑定客户"
