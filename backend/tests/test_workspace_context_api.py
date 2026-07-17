import pytest

from app.models import ClientMembership
from app.models.enums import WorkspaceRole


async def _token(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
