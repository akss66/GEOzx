"""共享知识库路由：爆款库/画像/提示词/话术条目 CRUD。

SPEC 5.2：知识库"全体可读可写"——属日常运营内容（非系统配置），故任意登录用户可增删改查，
按 org 隔离。Agent 也读取此库切片注入上下文（见 orchestrator 注入）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import KnowledgeEntry
from app.models.enums import KnowledgeCategory
from app.schemas.knowledge import (
    CreateKnowledgeRequest,
    KnowledgeOut,
    UpdateKnowledgeRequest,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _get_owned(session: AsyncSession, entry_id: int, org_id: int) -> KnowledgeEntry:
    entry = await session.get(KnowledgeEntry, entry_id)
    if entry is None or entry.org_id != org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    return entry


@router.get("", response_model=list[KnowledgeOut])
async def list_knowledge(
    user: CurrentUser,
    session: SessionDep,
    category: Annotated[KnowledgeCategory | None, Query()] = None,
) -> list[KnowledgeOut]:
    q = (
        select(KnowledgeEntry)
        .where(KnowledgeEntry.org_id == user.org_id)
        .order_by(KnowledgeEntry.id.desc())
    )
    if category is not None:
        q = q.where(KnowledgeEntry.category == category)
    rows = await session.scalars(q)
    return [KnowledgeOut.model_validate(k) for k in rows]


@router.post("", response_model=KnowledgeOut, status_code=status.HTTP_201_CREATED)
async def create_knowledge(
    body: CreateKnowledgeRequest, user: CurrentUser, session: SessionDep
) -> KnowledgeOut:
    entry = KnowledgeEntry(
        org_id=user.org_id,
        category=body.category,
        title=body.title,
        payload=body.payload,
        tags=body.tags,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return KnowledgeOut.model_validate(entry)


@router.patch("/{entry_id}", response_model=KnowledgeOut)
async def update_knowledge(
    entry_id: int, body: UpdateKnowledgeRequest, user: CurrentUser, session: SessionDep
) -> KnowledgeOut:
    entry = await _get_owned(session, entry_id, user.org_id)
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(entry, key, value)
    await session.commit()
    await session.refresh(entry)
    return KnowledgeOut.model_validate(entry)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge(entry_id: int, user: CurrentUser, session: SessionDep) -> None:
    entry = await _get_owned(session, entry_id, user.org_id)
    await session.delete(entry)
    await session.commit()
