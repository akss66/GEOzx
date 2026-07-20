from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.workspace_access import (
    accessible_account_clause,
    accessible_client_ids,
    accessible_project_ids,
)
from app.db import get_session
from app.models import Account, Client, Project
from app.schemas.shell import SearchResultOut

router = APIRouter(tags=["search"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/search", response_model=list[SearchResultOut])
async def search_workspace(
    user: CurrentUser,
    session: SessionDep,
    q: Annotated[str, Query(min_length=2, max_length=100)],
) -> list[SearchResultOut]:
    term = f"%{q.strip()}%"
    client_ids = await accessible_client_ids(session, user)
    if not client_ids:
        return []

    clients = (
        await session.scalars(
            select(Client)
            .where(Client.id.in_(client_ids), Client.name.ilike(term))
            .order_by(Client.id)
            .limit(20)
        )
    ).all()
    results = [
        SearchResultOut(
            kind="client",
            id=row.id,
            title=row.name,
            subtitle="客户",
            path="/",
            client_id=row.id,
        )
        for row in clients
    ]

    project_ids = await accessible_project_ids(session, user)
    project_candidates = (
        await session.scalars(
            select(Project)
            .where(
                Project.id.in_(project_ids),
                Project.client_id.in_(client_ids),
                Project.name.ilike(term),
            )
            .order_by(Project.id)
            .limit(20)
        )
    ).all()
    results.extend(
        SearchResultOut(
            kind="project",
            id=row.id,
            title=row.name,
            subtitle="项目",
            path="/",
            client_id=row.client_id,
            project_id=row.id,
        )
        for row in project_candidates
    )

    account_query = select(Account).where(
        Account.client_id.in_(client_ids),
        or_(Account.nickname.ilike(term), Account.external_account_id.ilike(term)),
        await accessible_account_clause(session, user),
    )
    accounts = (await session.scalars(account_query.order_by(Account.id).limit(20))).all()
    results.extend(
        SearchResultOut(
            kind="account",
            id=row.id,
            title=row.nickname,
            subtitle="抖音账号" if row.platform.value == "douyin" else "平台账号",
            path=f"/accounts?account={row.id}",
            client_id=row.client_id,
            project_id=row.project_id,
            account_id=row.id,
        )
        for row in accounts
    )
    return results[:20]
