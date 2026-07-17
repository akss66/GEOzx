import pytest
from fastapi import HTTPException

from app.core.workspace_access import require_client_access, require_project_access
from app.models import Client, ClientMembership, Project, ProjectMembership
from app.models.enums import WorkspaceRole


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
