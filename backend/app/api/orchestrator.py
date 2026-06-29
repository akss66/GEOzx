"""编排路由：创建内容、启动流水线、看板视图、质量门审批。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import AgentTask, ContentItem, Deliverable, GateApproval, Project
from app.orchestrator.engine import engine
from app.schemas.orchestrator import (
    AgentTaskOut,
    ApproveGateRequest,
    BoardOut,
    ContentItemOut,
    CreateContentItemRequest,
    DeliverableOut,
    GateApprovalOut,
)

router = APIRouter(tags=["orchestrator"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _board(session: AsyncSession, ci_id: int) -> BoardOut:
    ci = await session.get(ContentItem, ci_id)
    if ci is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="内容不存在")
    tasks = (
        await session.scalars(
            select(AgentTask).where(AgentTask.content_item_id == ci_id).order_by(AgentTask.id)
        )
    ).all()
    deliverables = (
        await session.scalars(
            select(Deliverable).where(Deliverable.content_item_id == ci_id).order_by(Deliverable.id)
        )
    ).all()
    gates = (
        await session.scalars(
            select(GateApproval)
            .where(GateApproval.content_item_id == ci_id)
            .order_by(GateApproval.id)
        )
    ).all()
    return BoardOut(
        content_item=ContentItemOut.model_validate(ci),
        tasks=[AgentTaskOut.model_validate(t) for t in tasks],
        deliverables=[DeliverableOut.model_validate(d) for d in deliverables],
        gates=[GateApprovalOut.model_validate(g) for g in gates],
    )


@router.post("/content-items", response_model=ContentItemOut, status_code=status.HTTP_201_CREATED)
async def create_content_item(
    body: CreateContentItemRequest, user: CurrentUser, session: SessionDep
) -> ContentItemOut:
    project = await session.get(Project, body.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
    ci = ContentItem(project_id=body.project_id, account_id=body.account_id, title=body.title)
    session.add(ci)
    await session.commit()
    await session.refresh(ci)
    return ContentItemOut.model_validate(ci)


@router.get("/content-items", response_model=list[ContentItemOut])
async def list_content_items(
    user: CurrentUser,
    session: SessionDep,
    project_id: Annotated[int | None, Query()] = None,
) -> list[ContentItemOut]:
    q = select(ContentItem).order_by(ContentItem.id.desc())
    if project_id is not None:
        q = q.where(ContentItem.project_id == project_id)
    rows = (await session.scalars(q)).all()
    return [ContentItemOut.model_validate(r) for r in rows]


@router.post("/content-items/{ci_id}/start", response_model=BoardOut)
async def start_pipeline(ci_id: int, user: CurrentUser, session: SessionDep) -> BoardOut:
    try:
        await engine.start(session, ci_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await _board(session, ci_id)


@router.get("/content-items/{ci_id}", response_model=BoardOut)
async def get_board(ci_id: int, user: CurrentUser, session: SessionDep) -> BoardOut:
    return await _board(session, ci_id)


@router.post("/gates/{approval_id}/approve", response_model=BoardOut)
async def approve_gate(
    approval_id: int,
    body: ApproveGateRequest,
    user: CurrentUser,
    session: SessionDep,
) -> BoardOut:
    try:
        ci = await engine.approve_gate(session, approval_id, user.id, body.approved, body.comment)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await _board(session, ci.id)
