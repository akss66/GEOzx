"""运营大脑 API。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import (
    Account,
    AccountGroup,
    AgentToolCall,
    BrainTask,
    DeliverableAcceptance,
    MatrixDistributionItem,
    MatrixDistributionPlan,
    OrchestrationPlan,
    Project,
    TaskBrief,
)
from app.models.enums import (
    AccountStatus,
    AgentCode,
    BrainTaskStatus,
    BrainTaskType,
    DeliverableAcceptanceStatus,
    Platform,
    RerunScope,
)
from app.orchestrator.brain_adapter import rerun_brain_acceptance, run_brain_task_pipeline
from app.schemas.brain import (
    AcceptDeliverableRequest,
    AgentInvocationOut,
    AgentToolCallOut,
    ApproveToolCallRequest,
    BrainTaskOut,
    CloseMemoryOut,
    DeliverableAcceptanceOut,
    DraftBrainTaskRequest,
    RejudgeDeliverableRequest,
    RerunDeliverableRequest,
)

router = APIRouter(prefix="/brain", tags=["brain"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _title(goal: str) -> str:
    return goal if len(goal) <= 32 else f"{goal[:32]}..."


def _infer_type(goal: str) -> BrainTaskType:
    if "复盘" in goal or "优化" in goal:
        return BrainTaskType.REVIEW_OPTIMIZATION
    if "诊断" in goal:
        return BrainTaskType.ACCOUNT_DIAGNOSIS
    if "矩阵" in goal or "分发" in goal:
        return BrainTaskType.MATRIX_DISTRIBUTION
    return BrainTaskType.CONTENT_CREATION


def _default_steps() -> list[dict]:
    return _enrich_step_contracts([
        {
            "id": "step-positioning",
            "agent_code": AgentCode.POSITIONING.value,
            "agent_name": "账号定位专家",
            "phase": "定位校准",
            "intent": "判断目标、账号组、人设和平台是否匹配。",
            "status": "planned",
            "depends_on": [],
            "expected_output": "定位策略",
            "risk_level": "low",
        },
        {
            "id": "step-script",
            "agent_code": AgentCode.CONTENT_DIRECTOR.value,
            "agent_name": "编导文案专家",
            "phase": "脚本生产",
            "intent": "产出可执行的视频脚本和分镜建议。",
            "status": "planned",
            "depends_on": ["step-positioning"],
            "expected_output": "脚本包",
            "risk_level": "medium",
        },
        {
            "id": "step-operation",
            "agent_code": AgentCode.OPERATOR.value,
            "agent_name": "账号运营专家",
            "phase": "发布与复盘",
            "intent": "汇总交付物、发布建议和下一轮复盘口径。",
            "status": "planned",
            "depends_on": ["step-script"],
            "expected_output": "发布计划与复盘建议",
            "risk_level": "medium",
        },
    ])


def _enrich_step_contracts(steps: list[dict]) -> list[dict]:
    contract = {
        "step-positioning": {
            "execution_kind": "account_diagnosis",
            "human_gate": False,
            "tool_codes": ["account_context", "profile_snapshot"],
        },
        "step-script": {
            "execution_kind": "content_generation",
            "human_gate": True,
            "tool_codes": ["brief_builder", "compliance_precheck"],
        },
        "step-art": {
            "execution_kind": "creative_generation",
            "human_gate": False,
            "tool_codes": ["style_prompt_builder"],
        },
        "step-video": {
            "execution_kind": "asset_preparation",
            "human_gate": True,
            "tool_codes": ["material_validator"],
        },
        "step-editing": {
            "execution_kind": "asset_preparation",
            "human_gate": True,
            "tool_codes": ["material_validator"],
        },
        "step-operation": {
            "execution_kind": "publish_readiness",
            "human_gate": True,
            "tool_codes": ["publish_package_prepare", "review_metrics"],
        },
    }
    return [{**step, **contract.get(step.get("id"), {})} for step in steps]


def _build_plan_steps(task_type: BrainTaskType) -> list[dict]:
    skipped_for_type: dict[BrainTaskType, set[str]] = {
        BrainTaskType.ACCOUNT_DIAGNOSIS: {"step-art", "step-video", "step-editing"},
        BrainTaskType.REVIEW_OPTIMIZATION: {"step-art", "step-video", "step-editing"},
        BrainTaskType.MATRIX_DISTRIBUTION: {"step-video", "step-editing"},
    }
    skipped = skipped_for_type.get(task_type, set())
    steps = [
        {
            "id": "step-positioning",
            "agent_code": AgentCode.POSITIONING.value,
            "agent_name": "账号定位专家",
            "phase": "定位校准",
            "intent": "判断目标、账号组、人设和平台是否匹配。",
            "status": "planned",
            "depends_on": [],
            "expected_output": "定位策略",
            "risk_level": "low",
        },
        {
            "id": "step-script",
            "agent_code": AgentCode.CONTENT_DIRECTOR.value,
            "agent_name": "编导文案专家",
            "phase": "脚本生产",
            "intent": "产出可执行的视频脚本和分镜建议。",
            "status": "planned",
            "depends_on": ["step-positioning"],
            "expected_output": "脚本包",
            "risk_level": "medium",
        },
        {
            "id": "step-art",
            "agent_code": AgentCode.ART_DIRECTOR.value,
            "agent_name": "美术指导提示词专家",
            "phase": "视觉提示词",
            "intent": "基于定位与脚本摘要并行准备视觉风格和镜头提示词。",
            "status": "planned",
            "depends_on": ["step-positioning"],
            "expected_output": "视觉风格书与提示词",
            "risk_level": "low",
        },
        {
            "id": "step-video",
            "agent_code": AgentCode.VIDEO_CREATOR.value,
            "agent_name": "视频创作专家",
            "phase": "视频生成",
            "intent": "汇合脚本与视觉提示词，生成视频素材或生成计划。",
            "status": "planned",
            "depends_on": ["step-script", "step-art"],
            "expected_output": "视频素材",
            "risk_level": "medium",
        },
        {
            "id": "step-editing",
            "agent_code": AgentCode.EDITOR.value,
            "agent_name": "剪辑专家",
            "phase": "成片剪辑",
            "intent": "将素材、字幕、节奏和平台比例转成可发布成片。",
            "status": "planned",
            "depends_on": ["step-video"],
            "expected_output": "成片",
            "risk_level": "medium",
        },
        {
            "id": "step-operation",
            "agent_code": AgentCode.OPERATOR.value,
            "agent_name": "账号运营专家",
            "phase": "发布与复盘",
            "intent": "汇总交付物、发布建议和下一轮复盘口径。",
            "status": "planned",
            "depends_on": ["step-editing"],
            "expected_output": "发布计划与复盘建议",
            "risk_level": "medium",
        },
    ]
    for step in steps:
        if step["id"] in skipped:
            step["status"] = "skipped"
    if "step-editing" in skipped:
        next(step for step in steps if step["id"] == "step-operation")["depends_on"] = [
            "step-script"
        ]
    return _enrich_step_contracts(steps)


async def _resolve_brief_bindings(
    session: AsyncSession,
    org_id: int,
    body: DraftBrainTaskRequest,
) -> dict:
    project_name = None
    if body.project_id is not None:
        project = await session.get(Project, body.project_id)
        if project is None or project.org_id != org_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")
        project_name = project.name

    account_group_name = None
    if body.account_group_id is not None:
        group = await session.get(AccountGroup, body.account_group_id)
        if group is None or group.org_id != org_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号组不存在")
        account_group_name = group.name

    platforms = [platform.value for platform in (body.platforms or [Platform.DOUYIN])]
    if any(platform != Platform.DOUYIN.value for platform in platforms):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="目前运营大脑仅支持抖音账号",
        )
    account_ids = sorted(set(body.account_ids))
    if not account_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先选择一个抖音账号，再启动运营大脑",
        )
    if account_ids:
        accounts = (
            await session.scalars(
                select(Account).where(Account.org_id == org_id, Account.id.in_(account_ids))
            )
        ).all()
        if len(accounts) != len(account_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
        if body.account_group_id is not None and any(
            account.group_id != body.account_group_id for account in accounts
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="账号不属于所选账号组",
            )
        if any(account.platform.value not in platforms for account in accounts):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="账号平台不在任务范围",
            )
        if any(account.status != AccountStatus.ACTIVE for account in accounts):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="账号状态不可用于运营任务",
            )
        if any(account.auth_status not in {"authorized", "manual"} for account in accounts):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="账号尚未授权，无法启动运营任务",
            )

    return {
        "project_name": project_name,
        "account_group_name": account_group_name,
        "platforms": platforms,
        "account_ids": account_ids,
    }


async def _load_task(session: AsyncSession, task_id: int, org_id: int) -> BrainTask:
    task = await session.scalar(
        select(BrainTask)
        .options(
            selectinload(BrainTask.brief),
            selectinload(BrainTask.plan),
            selectinload(BrainTask.invocations),
            selectinload(BrainTask.acceptances),
        )
        .where(BrainTask.id == task_id, BrainTask.org_id == org_id)
        .execution_options(populate_existing=True)
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="运营大脑任务不存在")
    return task


async def _load_acceptance(
    session: AsyncSession, task_id: int, org_id: int, acceptance_id: int
) -> DeliverableAcceptance:
    task = await _load_task(session, task_id, org_id)
    acceptance = next((row for row in task.acceptances if row.id == acceptance_id), None)
    if acceptance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="交付物验收记录不存在")
    return acceptance


@router.post("/tasks/draft", response_model=BrainTaskOut, status_code=status.HTTP_201_CREATED)
async def draft_task(
    body: DraftBrainTaskRequest, user: CurrentUser, session: SessionDep
) -> BrainTaskOut:
    task = await create_brain_task_draft(session, user.org_id, body)
    return BrainTaskOut.model_validate(await _load_task(session, task.id, user.org_id))


async def create_brain_task_draft(
    session: AsyncSession,
    org_id: int,
    body: DraftBrainTaskRequest,
) -> BrainTask:
    bindings = await _resolve_brief_bindings(session, org_id, body)
    risk_constraints = ["发布前必须过合规门", "高风险平台动作需要人工确认"]
    expected_outputs = ["定位策略", "脚本包", "视觉提示词", "发布计划", "复盘建议"]
    task_type = _infer_type(body.goal)
    task = BrainTask(
        org_id=org_id,
        title=_title(body.goal),
        type=task_type,
        status=BrainTaskStatus.PENDING_CONFIRMATION,
        progress=0,
        current_focus="等待用户确认任务 Brief",
        risk_count=len(risk_constraints),
    )
    task.brief = TaskBrief(
        goal=body.goal,
        project_id=body.project_id,
        project_name=bindings["project_name"],
        account_group_id=body.account_group_id,
        account_group_name=bindings["account_group_name"],
        platforms=bindings["platforms"],
        account_ids=bindings["account_ids"],
        cycle="待确认",
        budget=None,
        content_goal="根据目标生成一组可执行内容或优化动作。",
        risk_constraints=risk_constraints,
        expected_outputs=expected_outputs,
        confirmation_actions=["确认任务 Brief", "确认调度计划", "确认高风险动作"],
    )
    task.plan = OrchestrationPlan(
        summary="运营大脑已生成初步调度计划，等待确认后调用专家团。",
        steps=_build_plan_steps(task_type),
        quality_gates=["脚本合规", "发布前审核"],
        estimated_cost=Decimal("0.68"),
        requires_human_confirmation=True,
    )
    session.add(task)
    await session.commit()
    return task


@router.get("/tasks", response_model=list[BrainTaskOut])
async def list_tasks(user: CurrentUser, session: SessionDep) -> list[BrainTaskOut]:
    rows = (
        await session.scalars(
            select(BrainTask)
            .options(selectinload(BrainTask.brief), selectinload(BrainTask.plan))
            .where(BrainTask.org_id == user.org_id)
            .order_by(BrainTask.id.desc())
        )
    ).all()
    return [BrainTaskOut.model_validate(row) for row in rows]


@router.get("/tasks/{task_id}", response_model=BrainTaskOut)
async def get_task(task_id: int, user: CurrentUser, session: SessionDep) -> BrainTaskOut:
    return BrainTaskOut.model_validate(await _load_task(session, task_id, user.org_id))


@router.post("/tasks/{task_id}/confirm", response_model=BrainTaskOut)
async def confirm_task(task_id: int, user: CurrentUser, session: SessionDep) -> BrainTaskOut:
    task = await _load_task(session, task_id, user.org_id)
    await run_brain_task_pipeline(session, task)
    return BrainTaskOut.model_validate(await _load_task(session, task_id, user.org_id))


@router.get("/tasks/{task_id}/invocations", response_model=list[AgentInvocationOut])
async def list_invocations(
    task_id: int, user: CurrentUser, session: SessionDep
) -> list[AgentInvocationOut]:
    task = await _load_task(session, task_id, user.org_id)
    return [AgentInvocationOut.model_validate(row) for row in task.invocations]


@router.get("/tasks/{task_id}/tool-calls", response_model=list[AgentToolCallOut])
async def list_tool_calls(
    task_id: int, user: CurrentUser, session: SessionDep
) -> list[AgentToolCallOut]:
    await _load_task(session, task_id, user.org_id)
    rows = (
        await session.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.task_id == task_id, AgentToolCall.org_id == user.org_id)
            .order_by(AgentToolCall.id)
        )
    ).all()
    return [AgentToolCallOut.model_validate(row) for row in rows]


@router.get("/tool-calls/pending-approvals", response_model=list[AgentToolCallOut])
async def list_pending_tool_call_approvals(
    user: CurrentUser, session: SessionDep
) -> list[AgentToolCallOut]:
    rows = (
        await session.scalars(
            select(AgentToolCall)
            .where(
                AgentToolCall.org_id == user.org_id,
                AgentToolCall.requires_human_confirmation.is_(True),
                AgentToolCall.status == "waiting_approval",
            )
            .order_by(AgentToolCall.id)
        )
    ).all()
    return [AgentToolCallOut.model_validate(row) for row in rows]


async def _sync_matrix_distribution_approval(
    session: AsyncSession,
    org_id: int,
    meta: dict,
    approved: bool,
    comment: str | None,
) -> None:
    matrix_item_id = meta.get("matrix_item_id")
    matrix_plan_id = meta.get("matrix_plan_id")
    if not matrix_item_id or not matrix_plan_id:
        return

    item = await session.scalar(
        select(MatrixDistributionItem).where(
            MatrixDistributionItem.id == matrix_item_id,
            MatrixDistributionItem.org_id == org_id,
        )
    )
    plan = await session.scalar(
        select(MatrixDistributionPlan)
        .options(selectinload(MatrixDistributionPlan.items))
        .where(
            MatrixDistributionPlan.id == matrix_plan_id,
            MatrixDistributionPlan.org_id == org_id,
        )
    )
    if item is None or plan is None:
        return

    if approved:
        item.status = "queued"
        item.error = None
    else:
        item.status = "failed"
        item.error = comment or "Manual approval rejected"

    statuses = {row.status for row in plan.items}
    if statuses and statuses.issubset({"queued"}):
        plan.status = "queued"
    elif "failed" in statuses:
        plan.status = "failed"
    else:
        plan.status = "pending_approval"


@router.post("/tool-calls/{tool_call_id}/approve", response_model=AgentToolCallOut)
async def approve_tool_call(
    tool_call_id: int,
    body: ApproveToolCallRequest,
    user: CurrentUser,
    session: SessionDep,
) -> AgentToolCallOut:
    tool_call = await session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.id == tool_call_id,
            AgentToolCall.org_id == user.org_id,
        )
    )
    if tool_call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工具调用不存在")
    if not tool_call.requires_human_confirmation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该工具调用不需要人工确认")

    decision = {
        "approved": body.approved,
        "comment": body.comment or "",
        "reviewed_by": user.id,
        "reviewed_at": datetime.now(UTC).isoformat(),
    }
    next_meta = {**(tool_call.meta or {}), "decision": decision}
    if tool_call.tool_code == "publish_package_prepare" and body.approved:
        next_meta["publish_decision_status"] = "approved_for_manual_publish"
    elif tool_call.tool_code == "publish_package_prepare":
        next_meta["publish_decision_status"] = "rejected_for_manual_publish"
    tool_call.status = "success" if body.approved else "failed"
    tool_call.error = None if body.approved else body.comment or "人工确认未通过"
    tool_call.output_summary = (
        f"{tool_call.output_summary}\n人工确认：{'通过' if body.approved else '打回'}"
    ).strip()
    tool_call.meta = next_meta
    if tool_call.tool_code == "publish_package_prepare":
        await _sync_matrix_distribution_approval(
            session, user.org_id, next_meta, body.approved, body.comment
        )
    await session.commit()
    await session.refresh(tool_call)
    return AgentToolCallOut.model_validate(tool_call)


@router.get("/tasks/{task_id}/acceptances", response_model=list[DeliverableAcceptanceOut])
async def list_acceptances(
    task_id: int, user: CurrentUser, session: SessionDep
) -> list[DeliverableAcceptanceOut]:
    task = await _load_task(session, task_id, user.org_id)
    return [DeliverableAcceptanceOut.model_validate(row) for row in task.acceptances]


@router.post("/tasks/{task_id}/accept", response_model=DeliverableAcceptanceOut)
async def accept_deliverable(
    task_id: int, body: AcceptDeliverableRequest, user: CurrentUser, session: SessionDep
) -> DeliverableAcceptanceOut:
    acceptance = await _load_acceptance(session, task_id, user.org_id, body.acceptance_id)
    acceptance.status = DeliverableAcceptanceStatus.APPROVED
    acceptance.reviewer_note = body.reviewer_note or "用户已确认通过。"
    acceptance.rerun_scope = None
    task = await _load_task(session, task_id, user.org_id)
    if task.acceptances and all(
        row.status == DeliverableAcceptanceStatus.APPROVED for row in task.acceptances
    ):
        task.status = BrainTaskStatus.COMPLETED
        task.progress = 100
        task.current_focus = "等待用户关闭本次任务记忆"
    await session.commit()
    await session.refresh(acceptance)
    return DeliverableAcceptanceOut.model_validate(acceptance)


@router.post("/tasks/{task_id}/rerun", response_model=DeliverableAcceptanceOut)
async def rerun_deliverable(
    task_id: int, body: RerunDeliverableRequest, user: CurrentUser, session: SessionDep
) -> DeliverableAcceptanceOut:
    acceptance = await _load_acceptance(session, task_id, user.org_id, body.acceptance_id)
    task = await _load_task(session, task_id, user.org_id)
    acceptance = await rerun_brain_acceptance(
        session, task, acceptance, body.rerun_scope, body.reason
    )
    if body.ask_brain_rejudge:
        acceptance.brain_rejudge_summary = (
            acceptance.brain_rejudge_summary
            or "运营大脑建议保留已通过交付物，仅重跑受影响范围。"
        )
    await session.commit()
    await session.refresh(acceptance)
    return DeliverableAcceptanceOut.model_validate(acceptance)


@router.post("/tasks/{task_id}/rejudge", response_model=DeliverableAcceptanceOut)
async def rejudge_deliverable(
    task_id: int, body: RejudgeDeliverableRequest, user: CurrentUser, session: SessionDep
) -> DeliverableAcceptanceOut:
    acceptance = await _load_acceptance(session, task_id, user.org_id, body.acceptance_id)
    acceptance.status = DeliverableAcceptanceStatus.RERUN_REQUESTED
    acceptance.rerun_scope = acceptance.rerun_scope or RerunScope.CURRENT_AGENT
    acceptance.brain_rejudge_summary = (
        "运营大脑重新判断：问题集中在当前交付物，不建议全链重跑。"
    )
    acceptance.brain_rejudge_basis = [
        "问题未影响已通过的上游定位。",
        "下游依赖可在当前交付物更新后再同步刷新。",
    ]
    await session.commit()
    await session.refresh(acceptance)
    return DeliverableAcceptanceOut.model_validate(acceptance)


@router.post("/tasks/{task_id}/close-memory", response_model=CloseMemoryOut)
async def close_memory(task_id: int, user: CurrentUser, session: SessionDep) -> CloseMemoryOut:
    task = await _load_task(session, task_id, user.org_id)
    if task.status != BrainTaskStatus.COMPLETED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="任务尚未最终验收")
    task.context_closed_at = datetime.now(UTC)
    await session.commit()
    return CloseMemoryOut(task_id=task.id, closed=True, context_closed_at=task.context_closed_at)
