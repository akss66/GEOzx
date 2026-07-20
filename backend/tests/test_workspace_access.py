import pytest
from fastapi import HTTPException

from app.core.workspace_access import (
    accessible_account_ids,
    require_account_access,
    require_client_access,
    require_project_access,
)
from app.models import (
    Account,
    AccountMembership,
    Client,
    ClientMembership,
    Project,
    ProjectMembership,
)
from app.models.enums import Platform, WorkspaceRole


@pytest.mark.asyncio
async def test_member_cannot_read_unassigned_client(session, member) -> None:
    workspace = Client(org_id=member.org_id, name="未授权客户")
    session.add(workspace)
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await require_client_access(session, member, workspace.id)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_project_membership_overrides_client_role(session, member) -> None:
    workspace = Client(org_id=member.org_id, name="客户")
    project = Project(org_id=member.org_id, client=workspace, name="项目")
    session.add_all(
        [
            workspace,
            project,
            ClientMembership(
                client=workspace, user=member, role=WorkspaceRole.OPERATOR
            ),
            ProjectMembership(
                project=project, user=member, role=WorkspaceRole.REVIEWER
            ),
        ]
    )
    await session.commit()

    resolved = await require_project_access(
        session, member, project.id, roles={WorkspaceRole.REVIEWER}
    )

    assert resolved.id == project.id


@pytest.mark.asyncio
async def test_selected_account_scope_filters_workspace(session, member) -> None:
    workspace = Client(org_id=member.org_id, name="Scoped client")
    first_account = Account(
        org_id=member.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="Visible account",
    )
    second_account = Account(
        org_id=member.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="Hidden account",
    )
    member.account_scope_mode = "selected"
    session.add_all(
        [
            workspace,
            first_account,
            second_account,
            ClientMembership(
                client=workspace, user=member, role=WorkspaceRole.OPERATOR
            ),
            AccountMembership(user=member, account=first_account),
        ]
    )
    await session.commit()

    assert await accessible_account_ids(session, member) == {first_account.id}


@pytest.mark.asyncio
async def test_account_membership_never_grants_workspace_access(session, member) -> None:
    account = Account(
        org_id=member.org_id,
        platform=Platform.DOUYIN,
        nickname="Unassigned account",
    )
    member.account_scope_mode = "selected"
    session.add_all([account, AccountMembership(user=member, account=account)])
    await session.commit()

    assert await accessible_account_ids(session, member) == set()
    with pytest.raises(HTTPException) as exc:
        await require_account_access(session, member, account.id)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_selected_account_visibility_precedes_role_check(session, member) -> None:
    workspace = Client(org_id=member.org_id, name="Reviewer client")
    account = Account(
        org_id=member.org_id,
        client=workspace,
        platform=Platform.DOUYIN,
        nickname="Reviewer account",
    )
    member.account_scope_mode = "selected"
    session.add_all(
        [
            workspace,
            account,
            ClientMembership(
                client=workspace, user=member, role=WorkspaceRole.REVIEWER
            ),
            AccountMembership(user=member, account=account),
        ]
    )
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await require_account_access(
            session, member, account.id, roles={WorkspaceRole.OPERATOR}
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_administrator_account_access_is_unrestricted(session, admin) -> None:
    assert await accessible_account_ids(session, admin) is None
