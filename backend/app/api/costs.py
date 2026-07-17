"""Role-aware cost workspaces."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser
from app.db import get_session
from app.schemas.costs import CostOverviewOut, TechnicalCostOverviewOut
from app.services.cost_workspace import business_cost_overview, technical_cost_overview

router = APIRouter(prefix="/costs", tags=["costs"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/overview", response_model=CostOverviewOut)
async def get_cost_overview(
    user: CurrentUser,
    session: SessionDep,
    client_id: Annotated[int, Query(gt=0)],
    project_id: Annotated[int | None, Query(gt=0)] = None,
    days: Annotated[int, Query(ge=7, le=90)] = 30,
) -> CostOverviewOut:
    return await business_cost_overview(
        session,
        user=user,
        client_id=client_id,
        project_id=project_id,
        days=days,
    )


@router.get("/technical", response_model=TechnicalCostOverviewOut)
async def get_technical_cost_overview(
    admin: AdminUser,
    session: SessionDep,
    days: Annotated[int, Query(ge=7, le=90)] = 30,
) -> TechnicalCostOverviewOut:
    return await technical_cost_overview(session, user=admin, days=days)
