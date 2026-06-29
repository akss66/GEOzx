"""模型配置路由：per-Agent 首选/兜底模型（list 任意登录用户，改限 admin）。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser
from app.db import get_session
from app.models import ModelConfig
from app.schemas.configuration import ModelConfigOut, UpdateModelConfigRequest

router = APIRouter(prefix="/model-configs", tags=["model-configs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[ModelConfigOut])
async def list_model_configs(user: CurrentUser, session: SessionDep) -> list[ModelConfigOut]:
    rows = await session.scalars(
        select(ModelConfig)
        .where(ModelConfig.org_id == user.org_id)
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
