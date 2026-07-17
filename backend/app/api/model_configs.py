"""模型配置路由：per-Agent 首选/兜底模型（list 任意登录用户，改限 admin）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser
from app.db import get_session
from app.models import ModelConfig
from app.schemas.configuration import (
    CallStatus,
    ModelCallPageOut,
    ModelConfigOut,
    ModelInfrastructureOut,
    ModelProviderOut,
    ModelRouteOut,
    ProviderCode,
    UpdateModelConfigRequest,
    UpdateModelProviderRequest,
    UpdateModelRouteRequest,
)
from app.services.model_infrastructure import (
    infrastructure_overview,
    recent_calls,
    save_provider,
    save_route,
)

router = APIRouter(prefix="/model-configs", tags=["model-configs"])
infrastructure_router = APIRouter(
    prefix="/model-infrastructure", tags=["model-infrastructure"]
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[ModelConfigOut])
async def list_model_configs(admin: AdminUser, session: SessionDep) -> list[ModelConfigOut]:
    rows = await session.scalars(
        select(ModelConfig)
        .where(ModelConfig.org_id == admin.org_id)
        .order_by(ModelConfig.agent_code)
    )
    return [ModelConfigOut.model_validate(c) for c in rows]


@router.patch("/{config_id}", response_model=ModelConfigOut)
async def update_model_config(
    config_id: int, body: UpdateModelConfigRequest, admin: AdminUser, session: SessionDep
) -> ModelConfigOut:
    cfg = await session.get(ModelConfig, config_id)
    if cfg is None or cfg.org_id != admin.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模型配置不存在")
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(cfg, key, value)
    await session.commit()
    await session.refresh(cfg)
    return ModelConfigOut.model_validate(cfg)


@infrastructure_router.get("", response_model=ModelInfrastructureOut)
async def get_model_infrastructure(
    admin: AdminUser, session: SessionDep
) -> ModelInfrastructureOut:
    return ModelInfrastructureOut.model_validate(
        await infrastructure_overview(session, admin.org_id)
    )


@infrastructure_router.put("/providers/{provider}", response_model=ModelProviderOut)
async def update_model_provider(
    provider: ProviderCode,
    body: UpdateModelProviderRequest,
    admin: AdminUser,
    session: SessionDep,
) -> ModelProviderOut:
    return ModelProviderOut.model_validate(
        await save_provider(
            session,
            org_id=admin.org_id,
            user_id=admin.id,
            provider=provider,
            enabled=body.enabled,
            credential_ref=body.credential_ref,
        )
    )


@infrastructure_router.put("/routes/{agent_code}", response_model=ModelRouteOut)
async def update_model_route(
    agent_code: str,
    body: UpdateModelRouteRequest,
    admin: AdminUser,
    session: SessionDep,
) -> ModelRouteOut:
    return ModelRouteOut.model_validate(
        await save_route(
            session,
            org_id=admin.org_id,
            user_id=admin.id,
            agent_code=agent_code,
            **body.model_dump(),
        )
    )


@infrastructure_router.get("/calls", response_model=ModelCallPageOut)
async def list_model_calls(
    admin: AdminUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    status_filter: Annotated[CallStatus | None, Query(alias="status")] = None,
) -> ModelCallPageOut:
    return ModelCallPageOut.model_validate(
        await recent_calls(
            session,
            org_id=admin.org_id,
            limit=limit,
            call_status=status_filter,
        )
    )
