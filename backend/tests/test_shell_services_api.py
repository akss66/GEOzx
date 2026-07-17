import pytest

from app.models import Account, Client, ClientMembership, Notification, Project, ProjectMembership
from app.models.enums import Platform, WorkspaceRole


async def _token(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_search_never_returns_unassigned_client(client, admin, member, session) -> None:
    visible = Client(org_id=member.org_id, name="可见客户")
    hidden = Client(org_id=member.org_id, name="隐藏客户")
    session.add_all([visible, hidden])
    await session.flush()
    session.add(
        ClientMembership(
            client_id=visible.id,
            user_id=member.id,
            role=WorkspaceRole.OPERATOR,
        )
    )
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")

    response = await client.get("/search?q=客户", headers=_auth(token))

    assert response.status_code == 200
    ids = {(row["kind"], row["id"]) for row in response.json()}
    assert ("client", visible.id) in ids
    assert ("client", hidden.id) not in ids


@pytest.mark.asyncio
async def test_search_returns_account_for_project_scoped_member(
    client, admin, member, session
) -> None:
    workspace = Client(org_id=member.org_id, name="Project-only client")
    session.add(workspace)
    await session.flush()
    project = Project(
        org_id=member.org_id,
        client_id=workspace.id,
        name="Unrelated project name",
    )
    session.add(project)
    await session.flush()
    account = Account(
        org_id=member.org_id,
        client_id=workspace.id,
        project_id=project.id,
        platform=Platform.DOUYIN,
        external_account_id="project-only-open-id",
        nickname="Visible creator account",
    )
    session.add_all(
        [
            account,
            ProjectMembership(
                project_id=project.id,
                user_id=member.id,
                role=WorkspaceRole.OPERATOR,
            ),
        ]
    )
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")

    response = await client.get("/search?q=Visible", headers=_auth(token))

    assert response.status_code == 200
    assert any(
        row["kind"] == "account" and row["id"] == account.id
        for row in response.json()
    )


@pytest.mark.asyncio
async def test_notification_can_only_be_read_by_owner(client, member, session) -> None:
    notice = Notification(
        org_id=member.org_id,
        user_id=member.id,
        type="task.completed",
        title="任务完成",
    )
    session.add(notice)
    await session.commit()
    token = await _token(client, "user@test.com", "user-pw-123")

    response = await client.patch(
        f"/notifications/{notice.id}/read", headers=_auth(token)
    )

    assert response.status_code == 200
    assert response.json()["read_at"] is not None
