"""Reviewed, client-scoped knowledge documents and citation history."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import KnowledgeCitation, KnowledgeEntry
from app.models.enums import KnowledgeCategory
from app.schemas.knowledge import (
    CreateKnowledgeRequest,
    KnowledgeCitationOut,
    KnowledgeOut,
    UpdateKnowledgeRequest,
)
from app.services.knowledge_workspace import (
    get_scoped_entry,
    knowledge_event,
    list_scoped_knowledge,
    require_knowledge_scope,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[KnowledgeOut])
async def list_knowledge(
    user: CurrentUser,
    session: SessionDep,
    client_id: Annotated[int, Query(gt=0)],
    project_id: Annotated[int | None, Query(gt=0)] = None,
    category: Annotated[KnowledgeCategory | None, Query()] = None,
) -> list[KnowledgeOut]:
    rows = await list_scoped_knowledge(
        session,
        user,
        client_id=client_id,
        project_id=project_id,
        category=category,
    )
    return [KnowledgeOut.model_validate(row) for row in rows]


@router.post("", response_model=KnowledgeOut, status_code=status.HTTP_201_CREATED)
async def create_knowledge(
    body: CreateKnowledgeRequest, user: CurrentUser, session: SessionDep
) -> KnowledgeOut:
    await require_knowledge_scope(
        session,
        user,
        body.client_id,
        body.project_id,
        writable=True,
    )
    if body.source_type == "agent":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent 建议必须经过人工确认后才能写入知识库",
        )
    entry = KnowledgeEntry(
        org_id=user.org_id,
        client_id=body.client_id,
        project_id=body.project_id,
        category=body.category,
        title=body.title,
        content=body.content,
        payload=body.payload,
        tags=body.tags,
        source_type=body.source_type,
        source_label=body.source_label,
        source_url=str(body.source_url) if body.source_url else None,
        version=1,
        status="active",
        created_by_id=user.id,
    )
    session.add(entry)
    await session.flush()
    session.add(
        knowledge_event(
            "knowledge.created",
            project_id=entry.project_id,
            entry_id=entry.id,
            actor_user_id=user.id,
        )
    )
    await session.commit()
    await session.refresh(entry)
    return KnowledgeOut.model_validate(entry)


@router.patch("/{entry_id}", response_model=KnowledgeOut)
async def update_knowledge(
    entry_id: int, body: UpdateKnowledgeRequest, user: CurrentUser, session: SessionDep
) -> KnowledgeOut:
    entry = await get_scoped_entry(session, user, entry_id, writable=True)
    data = body.model_dump(exclude_unset=True)
    if "source_url" in data and data["source_url"] is not None:
        data["source_url"] = str(data["source_url"])
    if data:
        for key, value in data.items():
            setattr(entry, key, value)
        entry.version += 1
        session.add(
            knowledge_event(
                "knowledge.updated",
                project_id=entry.project_id,
                entry_id=entry.id,
                actor_user_id=user.id,
            )
        )
        await session.commit()
        await session.refresh(entry)
    return KnowledgeOut.model_validate(entry)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_knowledge(entry_id: int, user: CurrentUser, session: SessionDep) -> None:
    entry = await get_scoped_entry(session, user, entry_id, writable=True)
    entry.status = "archived"
    entry.version += 1
    session.add(
        knowledge_event(
            "knowledge.archived",
            project_id=entry.project_id,
            entry_id=entry.id,
            actor_user_id=user.id,
        )
    )
    await session.commit()


@router.get("/{entry_id}/citations", response_model=list[KnowledgeCitationOut])
async def list_knowledge_citations(
    entry_id: int,
    user: CurrentUser,
    session: SessionDep,
    client_id: Annotated[int, Query(gt=0)],
    project_id: Annotated[int | None, Query(gt=0)] = None,
) -> list[KnowledgeCitationOut]:
    entry = await get_scoped_entry(session, user, entry_id, writable=False)
    if entry.client_id != client_id or (
        entry.project_id is not None and entry.project_id != project_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    q = select(KnowledgeCitation).where(
        KnowledgeCitation.entry_id == entry.id,
        KnowledgeCitation.client_id == client_id,
    )
    if project_id is not None:
        q = q.where(KnowledgeCitation.project_id == project_id)
    rows = await session.scalars(q.order_by(KnowledgeCitation.id.desc()))
    return [KnowledgeCitationOut.model_validate(row) for row in rows]
