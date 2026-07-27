"""Verified operating experience shared with the AI COO runtime."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.workspace_access import (
    accessible_account_ids,
    accessible_client_ids,
    accessible_project_ids,
)
from app.db import get_session
from app.models import ExperienceMemory
from app.models.enums import UserRole
from app.schemas.ai_coo import ExperienceMemoryOut

router = APIRouter(prefix="/experience-memories", tags=["experience-memories"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[ExperienceMemoryOut])
async def list_experience_memories(
    user: CurrentUser,
    session: SessionDep,
    client_id: Annotated[int | None, Query(gt=0)] = None,
    project_id: Annotated[int | None, Query(gt=0)] = None,
    account_id: Annotated[int | None, Query(gt=0)] = None,
) -> list[ExperienceMemoryOut]:
    query = select(ExperienceMemory).where(
        ExperienceMemory.org_id == user.org_id,
        ExperienceMemory.status == "verified",
    )
    if user.role != UserRole.ADMIN:
        visible_accounts = await accessible_account_ids(session, user)
        visible_clients = await accessible_client_ids(session, user)
        visible_projects = await accessible_project_ids(session, user)
        scope_clauses = [
            and_(
                ExperienceMemory.account_id.is_(None),
                ExperienceMemory.client_id.is_(None),
                ExperienceMemory.project_id.is_(None),
            )
        ]
        if visible_accounts:
            scope_clauses.append(ExperienceMemory.account_id.in_(visible_accounts))
        if visible_clients:
            scope_clauses.append(ExperienceMemory.client_id.in_(visible_clients))
        if visible_projects:
            scope_clauses.append(ExperienceMemory.project_id.in_(visible_projects))
        query = query.where(or_(*scope_clauses))
    if client_id is not None:
        query = query.where(ExperienceMemory.client_id == client_id)
    if project_id is not None:
        query = query.where(ExperienceMemory.project_id == project_id)
    if account_id is not None:
        query = query.where(ExperienceMemory.account_id == account_id)
    rows = (await session.scalars(query.order_by(ExperienceMemory.id.desc()))).all()
    return [ExperienceMemoryOut.model_validate(row) for row in rows]
