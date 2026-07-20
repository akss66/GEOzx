"""闭环反馈 API：优化建议追踪。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.brain import create_brain_task_draft
from app.core.auth import CurrentUser
from app.core.workspace_access import (
    accessible_account_clause,
    accessible_project_ids,
    require_account_access,
    require_project_access,
)
from app.db import get_session
from app.models import Account, ContentItem, OptimizationSuggestion
from app.models.enums import OptimizationSuggestionStatus, WorkspaceRole
from app.schemas.brain import BrainTaskOut, DraftBrainTaskRequest
from app.schemas.feedback import (
    OptimizationSuggestionOut,
    UpdateOptimizationSuggestionRequest,
)

router = APIRouter(prefix="/optimization-suggestions", tags=["feedback"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
FEEDBACK_WRITE_ROLES = {WorkspaceRole.LEAD, WorkspaceRole.OPERATOR}


def _now() -> datetime:
    return datetime.now(UTC)


def _out(row: OptimizationSuggestion, content_title: str) -> OptimizationSuggestionOut:
    return OptimizationSuggestionOut(
        id=row.id,
        content_item_id=row.content_item_id,
        content_title=content_title,
        source_deliverable_id=row.source_deliverable_id,
        target_stage=row.target_stage,
        suggestion=row.suggestion,
        status=row.status,
        note=row.note,
        accepted_at=row.accepted_at,
        verified_at=row.verified_at,
        created_at=row.created_at,
    )


@router.get("", response_model=list[OptimizationSuggestionOut])
async def list_optimization_suggestions(
    user: CurrentUser,
    session: SessionDep,
    status_filter: Annotated[OptimizationSuggestionStatus | None, Query(alias="status")] = None,
) -> list[OptimizationSuggestionOut]:
    project_ids = await accessible_project_ids(session, user)
    if not project_ids:
        return []
    q = (
        select(OptimizationSuggestion, ContentItem.title)
        .join(ContentItem, OptimizationSuggestion.content_item_id == ContentItem.id)
        .where(
            OptimizationSuggestion.org_id == user.org_id,
            ContentItem.project_id.in_(project_ids),
            or_(
                ContentItem.account_id.is_(None),
                ContentItem.account_id.in_(
                    select(Account.id).where(await accessible_account_clause(session, user))
                ),
            ),
        )
        .order_by(OptimizationSuggestion.id.desc())
    )
    if status_filter is not None:
        q = q.where(OptimizationSuggestion.status == status_filter)
    rows = (await session.execute(q)).all()
    return [_out(row, title) for row, title in rows]


@router.patch("/{suggestion_id}", response_model=OptimizationSuggestionOut)
async def update_optimization_suggestion(
    suggestion_id: int,
    body: UpdateOptimizationSuggestionRequest,
    user: CurrentUser,
    session: SessionDep,
) -> OptimizationSuggestionOut:
    row = await session.scalar(
        select(OptimizationSuggestion).where(
            OptimizationSuggestion.id == suggestion_id,
            OptimizationSuggestion.org_id == user.org_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="优化建议不存在")
    content = await session.get(ContentItem, row.content_item_id)
    if content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="内容不存在")
    await require_project_access(
        session,
        user,
        content.project_id,
        roles=FEEDBACK_WRITE_ROLES,
    )
    if content.account_id is not None:
        await require_account_access(
            session,
            user,
            content.account_id,
            roles=FEEDBACK_WRITE_ROLES,
        )

    row.status = body.status
    row.note = body.note
    if body.status == OptimizationSuggestionStatus.ACCEPTED and row.accepted_at is None:
        row.accepted_at = _now()
    if body.status == OptimizationSuggestionStatus.VERIFIED:
        if row.accepted_at is None:
            row.accepted_at = _now()
        row.verified_at = _now()
    await session.commit()
    await session.refresh(row)

    title = await session.scalar(
        select(ContentItem.title).where(ContentItem.id == row.content_item_id)
    )
    return _out(row, title or "")


@router.post(
    "/{suggestion_id}/send-to-brain",
    response_model=BrainTaskOut,
    status_code=status.HTTP_201_CREATED,
)
async def send_suggestion_to_brain(
    suggestion_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> BrainTaskOut:
    row = await session.scalar(
        select(OptimizationSuggestion).where(
            OptimizationSuggestion.id == suggestion_id,
            OptimizationSuggestion.org_id == user.org_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="优化建议不存在")

    content = await session.get(ContentItem, row.content_item_id)
    if content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="内容不存在")
    await require_project_access(
        session,
        user,
        content.project_id,
        roles=FEEDBACK_WRITE_ROLES,
    )

    row.status = OptimizationSuggestionStatus.ACCEPTED
    row.note = row.note or "已送入运营大脑生成下一轮 Brief"
    if row.accepted_at is None:
        row.accepted_at = _now()

    stage = f"目标阶段：{row.target_stage}" if row.target_stage is not None else "目标阶段：待判断"
    task = await create_brain_task_draft(
        session,
        user,
        DraftBrainTaskRequest(
            goal=(
                f"基于《{content.title}》复盘建议生成下一轮优化任务。"
                f"{stage}；建议：{row.suggestion}"
            ),
            project_id=content.project_id,
            platforms=None,
            account_ids=[content.account_id] if content.account_id is not None else [],
        ),
    )
    return BrainTaskOut.model_validate(task)
