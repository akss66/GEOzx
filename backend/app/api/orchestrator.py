"""编排路由：创建内容、启动流水线、看板视图、质量门审批。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import (
    AgentTask,
    ComplianceCheck,
    ContentItem,
    Deliverable,
    GateApproval,
    Project,
)
from app.models.enums import GateStatus, GateType
from app.orchestrator.engine import engine
from app.schemas.orchestrator import (
    AgentTaskOut,
    ApproveGateRequest,
    BoardOut,
    ComplianceCheckOut,
    ContentItemOut,
    CreateContentItemRequest,
    DeliverableOut,
    GateApprovalOut,
    PendingGateOut,
    RerunStageRequest,
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
    checks = (
        await session.scalars(
            select(ComplianceCheck)
            .where(ComplianceCheck.content_item_id == ci_id)
            .order_by(ComplianceCheck.id)
        )
    ).all()
    return BoardOut(
        content_item=ContentItemOut.model_validate(ci),
        tasks=[AgentTaskOut.model_validate(t) for t in tasks],
        deliverables=[DeliverableOut.model_validate(d) for d in deliverables],
        gates=[GateApprovalOut.model_validate(g) for g in gates],
        compliance=[ComplianceCheckOut.model_validate(c) for c in checks],
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


@router.get("/content-items/{ci_id}/deliverables", response_model=list[DeliverableOut])
async def list_deliverable_history(
    ci_id: int, user: CurrentUser, session: SessionDep
) -> list[DeliverableOut]:
    """交付物全量历史（含 superseded 旧版），按 type + version 排序，供版本对比/回滚。"""
    rows = (
        await session.scalars(
            select(Deliverable)
            .where(Deliverable.content_item_id == ci_id)
            .order_by(Deliverable.type, Deliverable.version)
        )
    ).all()
    return [DeliverableOut.model_validate(d) for d in rows]


@router.post("/content-items/{ci_id}/rerun", response_model=BoardOut)
async def rerun_stage(
    ci_id: int, body: RerunStageRequest, user: CurrentUser, session: SessionDep
) -> BoardOut:
    """重跑某阶段 Agent，产新版交付物（旧版自动 superseded）。"""
    try:
        await engine.rerun_stage(session, ci_id, body.stage)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await _board(session, ci_id)


@router.post("/deliverables/{deliverable_id}/rollback", response_model=BoardOut)
async def rollback_deliverable(
    deliverable_id: int, user: CurrentUser, session: SessionDep
) -> BoardOut:
    """回滚到指定历史版本（设回 approved，其余同 type 版本 superseded）。"""
    try:
        d = await engine.rollback_deliverable(session, deliverable_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await _board(session, d.content_item_id)


@router.get("/gates", response_model=list[PendingGateOut])
async def list_pending_gates(user: CurrentUser, session: SessionDep) -> list[PendingGateOut]:
    """跨内容列出待审质量门（含内容标题 + 脚本合规门的合规预检结果），供审批中心用。"""
    rows = (
        await session.execute(
            select(GateApproval, ContentItem.title)
            .join(ContentItem, GateApproval.content_item_id == ContentItem.id)
            .where(GateApproval.status == GateStatus.PENDING)
            .order_by(GateApproval.id)
        )
    ).all()
    out: list[PendingGateOut] = []
    for g, title in rows:
        compliance = None
        if g.gate == GateType.SCRIPT_COMPLIANCE:
            check = await session.scalar(
                select(ComplianceCheck)
                .where(ComplianceCheck.content_item_id == g.content_item_id)
                .order_by(ComplianceCheck.id.desc())
            )
            if check is not None:
                compliance = ComplianceCheckOut.model_validate(check)
        out.append(
            PendingGateOut(
                id=g.id,
                gate=g.gate,
                status=g.status,
                content_item_id=g.content_item_id,
                content_title=title,
                created_at=g.created_at,
                compliance=compliance,
            )
        )
    return out


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
