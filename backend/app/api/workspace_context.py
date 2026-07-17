from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.workspace_access import (
    accessible_client_ids,
    require_client_access,
    require_project_access,
)
from app.db import get_session
from app.models import Account, Client, ClientMembership, Project, ProjectAccount
from app.models.enums import ClientStatus, UserRole
from app.schemas.client import ClientOut
from app.schemas.workspace import AccountOut, ProjectOut, account_out

router = APIRouter(tags=["workspace-context"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class WorkspaceContextOut(BaseModel):
    clients: list[ClientOut]
    selected_client: ClientOut | None
    projects: list[ProjectOut]
    selected_project: ProjectOut | None
    accounts: list[AccountOut]


@router.get("/workspace-context", response_model=WorkspaceContextOut)
async def get_workspace_context(
    user: CurrentUser,
    session: SessionDep,
    client_id: Annotated[int | None, Query()] = None,
    project_id: Annotated[int | None, Query()] = None,
) -> WorkspaceContextOut:
    client_ids = await accessible_client_ids(session, user)
    clients = []
    if client_ids:
        clients = (
            await session.scalars(
                select(Client)
                .where(Client.id.in_(client_ids), Client.status == ClientStatus.ACTIVE)
                .order_by(Client.id)
            )
        ).all()
    if not clients:
        return WorkspaceContextOut(
            clients=[], selected_client=None, projects=[], selected_project=None, accounts=[]
        )

    selected_client_id = client_id if client_id is not None else clients[0].id
    selected_client = await require_client_access(session, user, selected_client_id)
    if selected_client.status != ClientStatus.ACTIVE:
        return WorkspaceContextOut(
            clients=[ClientOut.model_validate(row) for row in clients],
            selected_client=None,
            projects=[],
            selected_project=None,
            accounts=[],
        )

    candidates = (
        await session.scalars(
            select(Project)
            .where(Project.client_id == selected_client.id)
            .order_by(Project.id)
        )
    ).all()
    projects: list[Project] = []
    for project in candidates:
        try:
            await require_project_access(session, user, project.id)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                continue
            raise
        projects.append(project)

    selected_project = None
    if project_id is not None:
        selected_project = await require_project_access(session, user, project_id)
        if selected_project.client_id != selected_client.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

    account_query = select(Account).where(Account.client_id == selected_client.id)
    allowed_project_ids = [project.id for project in projects]
    direct_membership = await session.scalar(
        select(ClientMembership.id).where(
            ClientMembership.client_id == selected_client.id,
            ClientMembership.user_id == user.id,
        )
    )
    if selected_project is not None:
        linked = select(ProjectAccount.account_id).where(
            ProjectAccount.project_id == selected_project.id
        )
        account_query = account_query.where(
            or_(Account.project_id == selected_project.id, Account.id.in_(linked))
        )
    elif direct_membership is None and user.role != UserRole.ADMIN:
        linked = select(ProjectAccount.account_id).where(
            ProjectAccount.project_id.in_(allowed_project_ids)
        )
        account_query = account_query.where(
            or_(Account.project_id.in_(allowed_project_ids), Account.id.in_(linked))
        )
    accounts = (await session.scalars(account_query.order_by(Account.id))).all()

    project_rows = []
    if accounts:
        project_rows = await session.execute(
            select(ProjectAccount.account_id, ProjectAccount.project_id).where(
                ProjectAccount.account_id.in_([account.id for account in accounts])
            )
        )
    project_ids_by_account: dict[int, list[int]] = {}
    for account_id_value, project_id_value in project_rows:
        project_ids_by_account.setdefault(account_id_value, []).append(project_id_value)

    return WorkspaceContextOut(
        clients=[ClientOut.model_validate(row) for row in clients],
        selected_client=ClientOut.model_validate(selected_client),
        projects=[ProjectOut.model_validate(row) for row in projects],
        selected_project=(
            ProjectOut.model_validate(selected_project) if selected_project is not None else None
        ),
        accounts=[
            account_out(account, project_ids_by_account.get(account.id)) for account in accounts
        ],
    )
