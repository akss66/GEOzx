"""Agent-proposed knowledge that requires a human decision."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import KnowledgeEntry, KnowledgeSuggestion
from app.schemas.knowledge import (
    CreateKnowledgeSuggestionRequest,
    KnowledgeSuggestionApprovalOut,
    KnowledgeSuggestionOut,
    ReviewKnowledgeSuggestionRequest,
)
from app.services.knowledge_workspace import (
    get_scoped_suggestion,
    knowledge_event,
    require_knowledge_scope,
    validate_suggestion_sources,
)

router = APIRouter(prefix="/knowledge-suggestions", tags=["knowledge"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=list[KnowledgeSuggestionOut])
async def list_suggestions(
    user: CurrentUser,
    session: SessionDep,
    client_id: Annotated[int, Query(gt=0)],
    project_id: Annotated[int | None, Query(gt=0)] = None,
    suggestion_status: Annotated[str, Query(alias="status")] = "pending",
) -> list[KnowledgeSuggestionOut]:
    await require_knowledge_scope(session, user, client_id, project_id, writable=False)
    q = select(KnowledgeSuggestion).where(
        KnowledgeSuggestion.org_id == user.org_id,
        KnowledgeSuggestion.client_id == client_id,
        KnowledgeSuggestion.status == suggestion_status,
    )
    if project_id is None:
        q = q.where(KnowledgeSuggestion.project_id.is_(None))
    else:
        q = q.where(
            or_(
                KnowledgeSuggestion.project_id.is_(None),
                KnowledgeSuggestion.project_id == project_id,
            )
        )
    rows = await session.scalars(q.order_by(KnowledgeSuggestion.id.desc()))
    return [KnowledgeSuggestionOut.model_validate(row) for row in rows]


@router.post("", response_model=KnowledgeSuggestionOut, status_code=status.HTTP_201_CREATED)
async def create_suggestion(
    body: CreateKnowledgeSuggestionRequest,
    user: CurrentUser,
    session: SessionDep,
) -> KnowledgeSuggestionOut:
    await require_knowledge_scope(
        session,
        user,
        body.client_id,
        body.project_id,
        writable=True,
    )
    await validate_suggestion_sources(
        session,
        user,
        task_id=body.source_task_id,
        deliverable_id=body.source_deliverable_id,
    )
    suggestion = KnowledgeSuggestion(
        org_id=user.org_id,
        **body.model_dump(),
        status="pending",
    )
    session.add(suggestion)
    await session.flush()
    session.add(
        knowledge_event(
            "knowledge.suggested",
            project_id=suggestion.project_id,
            suggestion_id=suggestion.id,
            actor_user_id=user.id,
        )
    )
    await session.commit()
    await session.refresh(suggestion)
    return KnowledgeSuggestionOut.model_validate(suggestion)


@router.post(
    "/{suggestion_id}/approve",
    response_model=KnowledgeSuggestionApprovalOut,
)
async def approve_suggestion(
    suggestion_id: int,
    body: ReviewKnowledgeSuggestionRequest,
    user: CurrentUser,
    session: SessionDep,
) -> KnowledgeSuggestionApprovalOut:
    suggestion = await get_scoped_suggestion(
        session, user, suggestion_id, writable=True
    )
    if suggestion.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该建议已经处理")
    entry = KnowledgeEntry(
        org_id=user.org_id,
        client_id=suggestion.client_id,
        project_id=suggestion.project_id,
        category=suggestion.category,
        title=suggestion.title,
        content=suggestion.content,
        payload=suggestion.payload,
        tags=suggestion.tags,
        source_type="agent",
        source_label=suggestion.source_label,
        version=1,
        status="active",
        created_by_id=user.id,
    )
    session.add(entry)
    await session.flush()
    suggestion.status = "approved"
    suggestion.reviewed_by_id = user.id
    suggestion.reviewed_at = datetime.now(UTC)
    suggestion.review_note = body.review_note
    suggestion.accepted_entry_id = entry.id
    session.add(
        knowledge_event(
            "knowledge.suggestion.approved",
            project_id=suggestion.project_id,
            entry_id=entry.id,
            suggestion_id=suggestion.id,
            actor_user_id=user.id,
        )
    )
    await session.commit()
    await session.refresh(entry)
    await session.refresh(suggestion)
    return KnowledgeSuggestionApprovalOut(suggestion=suggestion, entry=entry)


@router.post("/{suggestion_id}/reject", response_model=KnowledgeSuggestionOut)
async def reject_suggestion(
    suggestion_id: int,
    body: ReviewKnowledgeSuggestionRequest,
    user: CurrentUser,
    session: SessionDep,
) -> KnowledgeSuggestionOut:
    suggestion = await get_scoped_suggestion(
        session, user, suggestion_id, writable=True
    )
    if suggestion.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该建议已经处理")
    suggestion.status = "rejected"
    suggestion.reviewed_by_id = user.id
    suggestion.reviewed_at = datetime.now(UTC)
    suggestion.review_note = body.review_note
    session.add(
        knowledge_event(
            "knowledge.suggestion.rejected",
            project_id=suggestion.project_id,
            suggestion_id=suggestion.id,
            actor_user_id=user.id,
        )
    )
    await session.commit()
    await session.refresh(suggestion)
    return KnowledgeSuggestionOut.model_validate(suggestion)
