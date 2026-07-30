from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.workspace_access import (
    accessible_account_clause,
    accessible_client_ids,
    require_client_access,
    require_project_access,
)
from app.db import get_session
from app.models import (
    Account,
    AccountClient,
    Client,
    PlatformAccountAuth,
    Project,
    ProjectAccount,
)
from app.models.enums import ClientStatus
from app.schemas.client import ClientOut
from app.schemas.workspace import AccountOut, ProjectOut, account_out
from app.services.account_avatar import resolve_account_avatar_url

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
    clients: list[Client] = []
    if client_ids:
        clients = list(
            await session.scalars(
                select(Client)
                .where(Client.id.in_(client_ids), Client.status == ClientStatus.ACTIVE)
                .order_by(Client.id)
            )
        )
    selected_client = None
    projects: list[Project] = []
    selected_project = None
    if clients:
        selected_client_id = client_id if client_id is not None else clients[0].id
        selected_client = await require_client_access(session, user, selected_client_id)
        if selected_client.status == ClientStatus.ACTIVE:
            candidates = (
                await session.scalars(
                    select(Project)
                    .where(Project.client_id == selected_client.id)
                    .order_by(Project.id)
                )
            ).all()
            for project in candidates:
                try:
                    await require_project_access(session, user, project.id)
                except HTTPException as exc:
                    if exc.status_code == status.HTTP_404_NOT_FOUND:
                        continue
                    raise
                projects.append(project)

            if project_id is not None:
                selected_project = await require_project_access(session, user, project_id)
                if selected_project.client_id != selected_client.id:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="项目不存在",
                    )
        else:
            selected_client = None

    # Accounts are a first-class work context. Customer and project assignments
    # enrich that context but never hide an otherwise accessible account.
    accounts = (
        await session.scalars(
            select(Account)
            .where(await accessible_account_clause(session, user))
            .order_by(Account.id)
        )
    ).all()

    project_rows: list[tuple[int, int]] = []
    if accounts:
        project_rows = list(
            (
                await session.execute(
                    select(ProjectAccount.account_id, ProjectAccount.project_id).where(
                        ProjectAccount.account_id.in_([account.id for account in accounts])
                    )
                )
            ).tuples()
        )
    project_ids_by_account: dict[int, list[int]] = {}
    for account_id_value, project_id_value in project_rows:
        project_ids_by_account.setdefault(account_id_value, []).append(project_id_value)

    client_rows: list[tuple[int, int]] = []
    if accounts:
        client_rows = list(
            (
                await session.execute(
                    select(AccountClient.account_id, AccountClient.client_id).where(
                        AccountClient.account_id.in_([account.id for account in accounts])
                    )
                )
            ).tuples()
        )
    client_ids_by_account: dict[int, list[int]] = {}
    for account_id_value, client_id_value in client_rows:
        client_ids_by_account.setdefault(account_id_value, []).append(client_id_value)

    auth_by_account: dict[int, PlatformAccountAuth] = {}
    if accounts:
        auth_rows = (
            await session.scalars(
                select(PlatformAccountAuth).where(
                    PlatformAccountAuth.org_id == user.org_id,
                    PlatformAccountAuth.account_id.in_(
                        [account.id for account in accounts]
                    ),
                )
            )
        ).all()
        auth_by_account = {row.account_id: row for row in auth_rows}

    return WorkspaceContextOut(
        clients=[ClientOut.model_validate(row) for row in clients],
        selected_client=(
            ClientOut.model_validate(selected_client)
            if selected_client is not None
            else None
        ),
        projects=[ProjectOut.model_validate(row) for row in projects],
        selected_project=(
            ProjectOut.model_validate(selected_project) if selected_project is not None else None
        ),
        accounts=[
            account_out(
                account,
                project_ids_by_account.get(account.id),
                operational={
                    "avatar_url": resolve_account_avatar_url(
                        account,
                        auth_by_account.get(account.id),
                    )
                },
                client_ids=client_ids_by_account.get(account.id),
            )
            for account in accounts
        ],
    )
