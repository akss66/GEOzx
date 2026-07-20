"""Matrix distribution plan API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.approval_audit import add_approval_requested
from app.core.auth import CurrentUser
from app.core.workspace_access import accessible_account_ids
from app.db import get_session
from app.models import (
    Account,
    AgentToolCall,
    BrainTask,
    ContentItem,
    MaterialAsset,
    MatrixDistributionItem,
    MatrixDistributionPlan,
)
from app.models.enums import AccountStatus, BrainTaskStatus, BrainTaskType, Platform
from app.publishing.adapters import PublishDraft, get_publisher_adapter
from app.schemas.distribution import (
    CreateMatrixDistributionPlanRequest,
    MatrixDistributionPlanOut,
)

router = APIRouter(prefix="/matrix-distribution-plans", tags=["matrix-distribution"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _load_accounts(
    session: AsyncSession,
    user: CurrentUser,
    account_ids: list[int],
) -> list[Account]:
    unique_ids = sorted(set(account_ids))
    query = select(Account).where(Account.org_id == user.org_id, Account.id.in_(unique_ids))
    visible_account_ids = await accessible_account_ids(session, user)
    if visible_account_ids is not None:
        query = query.where(Account.id.in_(visible_account_ids))
    rows = (await session.scalars(query)).all()
    if len(rows) != len(unique_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    if any(account.status != AccountStatus.ACTIVE for account in rows):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="账号不可用于矩阵分发")
    if any(account.auth_status != "authorized" for account in rows):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="账号尚未授权，无法进入发布准备"
        )
    return sorted(rows, key=lambda account: account.id)


async def _load_materials(
    session: AsyncSession,
    org_id: int,
    material_ids: list[int],
    content_item_id: int | None,
) -> list[MaterialAsset]:
    unique_ids = sorted(set(material_ids))
    rows = (
        await session.scalars(
            select(MaterialAsset).where(
                MaterialAsset.org_id == org_id,
                MaterialAsset.id.in_(unique_ids),
            )
        )
    ).all()
    if len(rows) != len(unique_ids):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材不存在")
    if content_item_id is not None and any(row.content_item_id != content_item_id for row in rows):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="素材不属于当前内容")
    return sorted(rows, key=lambda material: material.id)


async def _load_plan(session: AsyncSession, org_id: int, plan_id: int) -> MatrixDistributionPlan:
    plan = await session.scalar(
        select(MatrixDistributionPlan)
        .options(selectinload(MatrixDistributionPlan.items))
        .where(MatrixDistributionPlan.org_id == org_id, MatrixDistributionPlan.id == plan_id)
    )
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="矩阵分发计划不存在")
    plan.items.sort(key=lambda item: item.id)
    return plan


@router.post("", response_model=MatrixDistributionPlanOut, status_code=status.HTTP_201_CREATED)
async def create_matrix_distribution_plan(
    body: CreateMatrixDistributionPlanRequest,
    user: CurrentUser,
    session: SessionDep,
) -> MatrixDistributionPlanOut:
    platforms = sorted(set(body.platforms), key=lambda platform: platform.value)
    accounts = await _load_accounts(session, user, body.account_ids)
    materials = await _load_materials(session, user.org_id, body.material_ids, body.content_item_id)
    account_platforms = {account.platform for account in accounts}
    if not account_platforms.issubset(set(platforms)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="账号平台不在矩阵计划范围"
        )

    plan = MatrixDistributionPlan(
        org_id=user.org_id,
        content_item_id=body.content_item_id,
        created_by_id=user.id,
        title=body.title.strip(),
        body=body.body.strip(),
        platforms=[platform.value for platform in platforms],
        account_ids=[account.id for account in accounts],
        material_ids=[material.id for material in materials],
        topics=[topic.strip() for topic in body.topics if topic.strip()],
        cover_material_id=body.cover_material_id,
        scheduled_at=body.scheduled_at,
        status="pending_approval",
    )
    task = BrainTask(
        org_id=user.org_id,
        created_by_id=user.id,
        content_item_id=body.content_item_id,
        title=f"Matrix distribution: {body.title.strip()}",
        type=BrainTaskType.MATRIX_DISTRIBUTION,
        status=BrainTaskStatus.PENDING_ACCEPTANCE,
        progress=80,
        current_focus="矩阵发布包等待人工审批",
    )
    session.add_all([plan, task])
    await session.flush()

    project_id: int | None = None
    if plan.content_item_id is not None:
        content_item = await session.get(ContentItem, plan.content_item_id)
        project_id = content_item.project_id if content_item is not None else None
    else:
        account_project_ids = {
            account.project_id for account in accounts if account.project_id is not None
        }
        if len(account_project_ids) == 1:
            project_id = next(iter(account_project_ids))

    draft = PublishDraft(
        title=plan.title,
        body=plan.body,
        topics=plan.topics,
        scheduled_at=plan.scheduled_at,
        cover_material_id=plan.cover_material_id,
    )
    for account in accounts:
        adapter = get_publisher_adapter(Platform(account.platform))
        for material in materials:
            package = adapter.prepare_publish_package(account, material, draft)
            item = MatrixDistributionItem(
                org_id=user.org_id,
                plan_id=plan.id,
                account_id=account.id,
                material_id=material.id,
                platform=account.platform.value,
                status="waiting_manual",
                publish_package=package.model_dump(mode="json"),
            )
            session.add(item)
            await session.flush()
            tool_call = AgentToolCall(
                org_id=user.org_id,
                task_id=task.id,
                module="matrix_distribution",
                agent_code="06-operator",
                tool_code="publish_package_prepare",
                tool_name="Publish Package Prepare",
                status="waiting_approval",
                permission_mode="confirm",
                requires_human_confirmation=True,
                input_summary=(
                    f"{account.platform.value} matrix publish package "
                    f"for account #{account.id}"
                ),
                output_summary="Matrix publish package ready for manual approval",
                meta={
                    "matrix_plan_id": plan.id,
                    "matrix_item_id": item.id,
                    "content_item_id": plan.content_item_id,
                    "platform": account.platform.value,
                    "account_id": account.id,
                    "material_id": material.id,
                    "publish_package": package.model_dump(mode="json"),
                    "risk": "pass",
                    "findings": [],
                },
            )
            session.add(tool_call)
            await session.flush()
            item.tool_call_id = tool_call.id

    await add_approval_requested(
        session,
        org_id=user.org_id,
        project_id=project_id,
        content_item_id=plan.content_item_id,
        approval_kind="matrix_plan",
        source_id=plan.id,
        title=plan.title,
        body=f"矩阵分发计划包含 {len(accounts) * len(materials)} 个发布包。",
    )

    await session.commit()
    return MatrixDistributionPlanOut.model_validate(await _load_plan(session, user.org_id, plan.id))


@router.get("", response_model=list[MatrixDistributionPlanOut])
async def list_matrix_distribution_plans(
    user: CurrentUser,
    session: SessionDep,
) -> list[MatrixDistributionPlanOut]:
    plans = (
        await session.scalars(
            select(MatrixDistributionPlan)
            .options(selectinload(MatrixDistributionPlan.items))
            .where(MatrixDistributionPlan.org_id == user.org_id)
            .order_by(MatrixDistributionPlan.id.desc())
        )
    ).all()
    for plan in plans:
        plan.items.sort(key=lambda item: item.id)
    return [MatrixDistributionPlanOut.model_validate(plan) for plan in plans]
