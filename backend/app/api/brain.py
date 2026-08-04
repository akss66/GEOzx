"""运营大脑 API。"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.approval_access import (
    require_task_approval_access,
    require_task_visibility,
    task_project_ids,
)
from app.core.approval_audit import add_approval_decided
from app.core.auth import AdminUser, CurrentUser
from app.core.runtime_failures import (
    FailureDisposition,
    describe_runtime_failure,
    exception_chain,
)
from app.core.workspace_access import (
    accessible_account_ids,
    require_project_access,
)
from app.db import get_session
from app.models import (
    Account,
    AccountGroup,
    AgentQualityScore,
    AgentRun,
    AgentToolCall,
    BrainTask,
    DecisionTrace,
    DeliverableAcceptance,
    ExperienceMemory,
    LLMCall,
    MatrixDistributionItem,
    MatrixDistributionPlan,
    OrchestrationPlan,
    ReflectionRecord,
    SkillRun,
    StrategyPlan,
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
    UserRole,
)
from app.orchestrator.brain_adapter import rerun_brain_acceptance
from app.orchestrator.brain_intelligence import IntelligenceUnavailable, brain_intelligence
from app.orchestrator.brain_planner import PlanningDecision, brain_planner
from app.orchestrator.brain_runtime import (
    next_actions,
    runtime_events,
    runtime_graph,
    runtime_status,
)
from app.orchestrator.generation_control import generation_control
from app.schemas.ai_coo import (
    AgentQualityScoreOut,
    DecisionTraceOut,
    ExperienceMemoryOut,
    ExperienceVerificationRequest,
    OperationIntelligenceOut,
    ReflectionRecordOut,
    StrategyPlanOut,
)
from app.schemas.brain import (
    AcceptDeliverableRequest,
    AgentInvocationOut,
    AgentToolCallOut,
    ApproveToolCallRequest,
    BrainMessageRequest,
    BrainRuntimeOut,
    BrainTaskOut,
    CloseMemoryOut,
    DecisionRequest,
    DecisionRevisionRequest,
    DecisionSelectionRequest,
    DeliverableAcceptanceOut,
    DraftBrainTaskRequest,
    IntentDecision,
    LLMCallAuditOut,
    RegenerateBrainMessageRequest,
    RejudgeDeliverableRequest,
    RerunDeliverableRequest,
    RuntimeEventOut,
    StopBrainGenerationOut,
    StopBrainGenerationRequest,
    route_decision_from_legacy_intent,
)
from app.services.agent_management import quality_gate_labels, require_agent_enabled
from app.services.agent_runs import (
    abort_agent_runtime,
    claim_agent_run,
    complete_agent_run,
    enqueue_agent_runtime,
    get_agent_run,
    mark_agent_run_queued,
    queue_agent_run_behind_task,
    release_agent_run_failure,
    request_agent_run_cancel,
    utc_now,
)
from app.services.ai_coo_learning import ai_coo_learning_service
from app.services.composite_skill_runs import lock_composite_finish_approval
from app.services.publishing import sync_publish_jobs_after_approval
from app.services.runtime_state import (
    publish_runtime_state_intents,
    replay_runtime_state_events,
)
from app.services.skill_approvals import (
    SkillApprovalConflict,
    finalize_skill_finish_approval,
)
from app.services.turn_interrupts import (
    request_interrupt,
    request_stop,
    resolve_interrupt,
)

router = APIRouter(prefix="/brain", tags=["brain"])
log = logging.getLogger("dyflow.brain")


class BriefBindings(TypedDict):
    project_name: str | None
    account_group_name: str | None
    platforms: list[str]
    account_ids: list[int]

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def _queue_runtime_resume(
    session: AsyncSession,
    *,
    user: CurrentUser,
    task: BrainTask,
    idempotency_key: str,
    request_payload: dict,
) -> AgentRun:
    run, claimed = await claim_agent_run(
        session,
        org_id=user.org_id,
        requested_by_id=user.id,
        client_message_id=idempotency_key,
        request_payload=request_payload,
    )
    if claimed:
        await mark_agent_run_queued(
            session,
            run.id,
            task_id=task.id,
            request_payload=request_payload,
        )
        await _submit_durable_agent_run(run.id)
    return run


async def _submit_durable_agent_run(run_id: int) -> None:
    try:
        await enqueue_agent_runtime(run_id=run_id)
    except Exception:  # noqa: BLE001 - the database run is the durable outbox
        log.exception(
            "AgentRun #%s is durable but could not be submitted; recovery will retry",
            run_id,
        )


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


def _is_casual_goal(goal: str) -> bool:
    normalized = "".join(
        ch for ch in goal.lower().strip() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
    )
    if not normalized:
        return True
    workflow_keywords = {
        "诊断",
        "分析",
        "生成",
        "脚本",
        "内容",
        "选题",
        "发布",
        "复盘",
        "矩阵",
        "分发",
        "账号",
        "抖音",
        "小红书",
        "视频号",
        "运营",
        "策划",
        "计划",
        "素材",
        "文案",
        "标题",
        "增长",
        "数据",
        "粉丝",
        "互动",
        "评论",
        "审核",
        "合规",
    }
    if any(keyword in normalized for keyword in workflow_keywords):
        return False
    casual_exact = {
        "你好",
        "您好",
        "hi",
        "hello",
        "哈喽",
        "在吗",
        "在不在",
        "测试",
        "ok",
        "谢谢",
        "好的",
        "好",
    }
    return normalized in casual_exact or len(normalized) <= 8


def _default_steps() -> list[dict]:
    return _enrich_step_contracts(
        [
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
        ]
    )


def _enrich_step_contracts(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contract: dict[str, dict[str, Any]] = {
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
    enriched: list[dict[str, Any]] = []
    for step in steps:
        step_id = step.get("id")
        contract_values = contract.get(step_id, {}) if isinstance(step_id, str) else {}
        enriched.append({**step, **contract_values})
    return enriched


def _build_plan_steps(task_type: BrainTaskType) -> list[dict[str, Any]]:
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
    user: CurrentUser,
    body: DraftBrainTaskRequest,
) -> BriefBindings:
    org_id = user.org_id
    project_name = None
    if body.project_id is not None:
        project = await require_project_access(session, user, body.project_id)
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
        visible_account_ids = await accessible_account_ids(session, user)
        account_query = select(Account).where(
            Account.org_id == org_id, Account.id.in_(account_ids)
        )
        if visible_account_ids is not None:
            account_query = account_query.where(Account.id.in_(visible_account_ids))
        accounts = (
            await session.scalars(account_query)
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


async def _load_task_for_user(session: AsyncSession, task_id: int, user: CurrentUser) -> BrainTask:
    task = await _load_task(session, task_id, user.org_id)
    await require_task_visibility(session, user, task)
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
    task = await create_brain_task_draft(session, user, body)
    return BrainTaskOut.model_validate(await _load_task(session, task.id, user.org_id))


async def create_brain_task_draft(
    session: AsyncSession,
    user: CurrentUser,
    body: DraftBrainTaskRequest,
) -> BrainTask:
    org_id = user.org_id
    bindings = await _resolve_brief_bindings(session, user, body)
    is_casual = _is_casual_goal(body.goal)
    if not is_casual:
        await require_agent_enabled(session, org_id, AgentCode.DECISION)
    risk_constraints = [] if is_casual else ["发布前必须过合规门", "高风险平台动作需要人工确认"]
    task_type = _infer_type(body.goal)
    if is_casual:
        planning = PlanningDecision([], "", "intent", [])
    else:
        planning = await brain_planner.plan(session, org_id, body.goal, task_type)
    if not is_casual and not planning.steps:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前目标没有可用专家，请先在专家管理中启用对应专家",
        )
    plan_steps = planning.steps
    plan_summary = (
        "杩愯惀澶ц剳灏嗙洿鎺ュ洖搴旇繖鏉℃櫘閫氭秷鎭紝涓嶈皟鐢ㄤ笓瀹跺洟銆?"
        if is_casual
        else planning.summary
    )
    plan_quality_gates = quality_gate_labels(planning.quality_gates)
    expected_outputs = (
        ["运营大脑普通回复"]
        if is_casual
        else [str(step["expected_output"]) for step in plan_steps]
    )
    task = BrainTask(
        org_id=org_id,
        created_by_id=user.id,
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
        content_goal=(
            "普通对话，不启动专家工作流。"
            if is_casual
            else "根据目标生成一组可执行内容或优化动作。"
        ),
        risk_constraints=risk_constraints,
        expected_outputs=expected_outputs,
        confirmation_actions=(
            [] if is_casual else ["确认任务目标", "确认调度计划", "确认高风险动作"]
        ),
    )
    task.plan = OrchestrationPlan(
        summary=(
            "运营大脑将直接回应这条普通消息，不调用专家团。" if is_casual else planning.summary
        ),
        steps=plan_steps,
        quality_gates=[] if is_casual else quality_gate_labels(planning.quality_gates),
        estimated_cost=Decimal("0.00") if is_casual else Decimal("0.68"),
        requires_human_confirmation=not is_casual,
    )
    task.plan.summary = plan_summary
    task.plan.quality_gates = plan_quality_gates
    session.add(task)
    await session.commit()
    return task


@router.post("/messages", response_model=BrainRuntimeOut, status_code=status.HTTP_201_CREATED)
async def send_brain_message(
    body: BrainMessageRequest,
    user: CurrentUser,
    session: SessionDep,
) -> BrainRuntimeOut:
    return await _send_brain_message(body, user, session)


async def _send_brain_message(
    body: BrainMessageRequest,
    user: CurrentUser,
    session: AsyncSession,
    *,
    regeneration_source_event_id: int | None = None,
) -> BrainRuntimeOut:
    client_message_id = body.client_message_id or uuid4().hex
    body = body.model_copy(update={"client_message_id": client_message_id})
    run, claimed = await claim_agent_run(
        session,
        org_id=user.org_id,
        requested_by_id=user.id,
        client_message_id=client_message_id,
        request_payload={
            "message": body.message,
            "task_id": body.task_id,
            "project_id": body.project_id,
            "account_id": body.account_id,
            "platform": body.platform.value,
            "regeneration_source_event_id": regeneration_source_event_id,
        },
    )
    if not claimed:
        if run.task_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "agent_run_in_progress",
                    "message": "This message is already being processed.",
                    "client_message_id": client_message_id,
                },
            )
        existing_task = await _load_task_for_user(session, run.task_id, user)
        return await _runtime_response(session, existing_task, user)

    run_id = run.id
    if settings.agent_runtime_async_enabled and body.task_id is not None:
        existing_task = await _load_task_for_user(session, body.task_id, user)
        waiting_payload = {
            "operation": "prepare_and_start",
            "message": body.message,
            "task_id": body.task_id,
            "project_id": body.project_id,
            "account_id": body.account_id,
            "platform": body.platform.value,
            "client_message_id": client_message_id,
            "regeneration_source_event_id": regeneration_source_event_id,
            "user_message_recorded": True,
        }
        queued_behind_predecessor = await queue_agent_run_behind_task(
            session,
            run_id,
            task_id=body.task_id,
            request_payload=waiting_payload,
        )
        if queued_behind_predecessor:
            await runtime_graph.record_user_message(
                session,
                existing_task,
                body.message,
                client_message_id=client_message_id,
            )
            return await _runtime_response(session, existing_task, user)

    run.status = "running"
    run.phase = "request"
    run.started_at = utc_now()
    if not settings.agent_runtime_async_enabled:
        run.attempt += 1
    await session.commit()
    try:
        response = await _execute_brain_message(
            body,
            user,
            session,
            agent_run_id=run_id,
            agent_run_attempt=run.attempt,
            regeneration_source_event_id=regeneration_source_event_id,
        )
    except Exception as exc:
        failure = describe_runtime_failure(exc)
        if failure.disposition is FailureDisposition.RETRYABLE:
            message = "任务暂时无法完成，请稍后重试。"
            recovery_action = "请稍后重新提交本次任务。"
        else:
            message = failure.message
            recovery_action = failure.recovery_action
        await release_agent_run_failure(
            session,
            run_id,
            disposition=FailureDisposition.TERMINAL,
            error_code=failure.error_code,
            error_detail=message,
            user_message=message,
            recovery_action=recovery_action,
        )
        response_status = next(
            (
                item.status_code
                for item in exception_chain(exc)
                if isinstance(item, HTTPException) and 400 <= item.status_code < 600
            ),
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        raise HTTPException(status_code=response_status, detail=message) from exc

    if not settings.agent_runtime_async_enabled:
        await complete_agent_run(
            session,
            run_id,
            task_id=response.task.id,
            status=response.status,
        )
    return response


async def _execute_brain_message(
    body: BrainMessageRequest,
    user: CurrentUser,
    session: AsyncSession,
    *,
    agent_run_id: int,
    agent_run_attempt: int,
    regeneration_source_event_id: int | None = None,
    force_inline: bool = False,
    user_message_recorded: bool = False,
    execution_owner: str | None = None,
) -> BrainRuntimeOut:
    task = (
        await _load_task_for_user(session, body.task_id, user)
        if body.task_id is not None
        else None
    )
    if task is not None:
        run = await session.get(AgentRun, agent_run_id)
        if run is not None:
            run.task_id = task.id
            await session.commit()
    existing_account_ids = list(task.brief.account_ids) if task and task.brief else []
    existing_project_id = task.brief.project_id if task and task.brief else None

    if body.account_id is not None and existing_account_ids:
        if body.account_id not in existing_account_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="当前对话已绑定其他账号，请开启新对话后切换账号",
            )
    if body.project_id is not None and existing_project_id is not None:
        if body.project_id != existing_project_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="当前对话已绑定其他项目，请开启新对话后切换项目",
            )

    effective_account_id = body.account_id or (
        existing_account_ids[0] if existing_account_ids else None
    )
    effective_project_id = body.project_id or existing_project_id

    try:
        intent = await brain_intelligence.classify(
            session,
            user.org_id,
            body.message,
            has_account=effective_account_id is not None,
            platform=body.platform.value,
        )
    except IntelligenceUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    route_decision = intent.route_decision or route_decision_from_legacy_intent(
        intent,
        has_account=effective_account_id is not None,
    )
    if intent.route_decision is None:
        intent = intent.model_copy(update={"route_decision": route_decision})
    run = await session.get(AgentRun, agent_run_id)
    if run is not None:
        run.request_payload = {
            **dict(run.request_payload or {}),
            "route_decision": route_decision.model_dump(mode="json"),
        }
        await session.commit()

    bindings: BriefBindings = {
        "project_name": task.brief.project_name if task and task.brief else None,
        "account_group_name": None,
        "platforms": list(task.brief.platforms)
        if task and task.brief and task.brief.platforms
        else [body.platform.value],
        "account_ids": existing_account_ids,
    }
    if intent.requires_account_context or effective_account_id is not None:
        bindings = await _resolve_brief_bindings(
            session,
            user,
            DraftBrainTaskRequest(
                goal=body.message,
                project_id=effective_project_id,
                platforms=[body.platform],
                account_ids=[effective_account_id] if effective_account_id is not None else [],
            ),
        )

    await require_agent_enabled(session, user.org_id, AgentCode.DECISION)
    task_type = _infer_type(body.message)
    planning = await brain_planner.plan_selected(
        session,
        user.org_id,
        [code.value for code in intent.suggested_expert_codes],
        intent.reason,
    )
    risk_constraints = (
        ["高风险外部动作必须由用户确认"] if intent.intent == "action" else []
    )
    confirmation_actions = ["高风险动作单独确认"] if intent.intent == "action" else []
    expected_outputs = [str(step["expected_output"]) for step in planning.steps]

    if task is None:
        task = BrainTask(
            org_id=user.org_id,
            created_by_id=user.id,
            title=_title(body.message),
            type=task_type,
            status=BrainTaskStatus.RUNNING,
            progress=0,
            current_focus="运营大脑正在理解你的消息",
            risk_count=1 if intent.intent == "action" else 0,
            runtime_mode="langgraph",
        )
        task.brief = TaskBrief(
            goal=body.message,
            project_id=effective_project_id,
            project_name=bindings["project_name"],
            account_group_id=None,
            account_group_name=None,
            platforms=bindings["platforms"],
            account_ids=bindings["account_ids"],
            cycle="当前对话",
            budget=None,
            content_goal="由运营大脑根据对话动态决定下一步。",
            risk_constraints=risk_constraints,
            expected_outputs=expected_outputs,
            confirmation_actions=confirmation_actions,
        )
        task.plan = OrchestrationPlan(
            summary=planning.summary,
            steps=planning.steps,
            quality_gates=quality_gate_labels(planning.quality_gates),
            estimated_cost=Decimal("0.00"),
            requires_human_confirmation=intent.intent == "action",
        )
        session.add(task)
    else:
        if task.brief is None or task.plan is None:
            raise RuntimeError("brain task draft is missing brief or plan")
        task.type = task_type
        task.status = BrainTaskStatus.RUNNING
        task.progress = 0
        task.current_focus = "运营大脑正在理解你的新消息"
        task.risk_count = 1 if intent.intent == "action" else 0
        task.runtime_mode = "langgraph"
        task.brief.goal = body.message
        task.brief.project_id = effective_project_id
        task.brief.project_name = bindings["project_name"]
        task.brief.platforms = bindings["platforms"]
        task.brief.account_ids = bindings["account_ids"]
        task.brief.content_goal = "由运营大脑根据本轮消息动态决定下一步。"
        task.brief.risk_constraints = risk_constraints
        task.brief.expected_outputs = expected_outputs
        task.brief.confirmation_actions = confirmation_actions
        task.plan.summary = planning.summary
        task.plan.steps = planning.steps
        task.plan.quality_gates = quality_gate_labels(planning.quality_gates)
        task.plan.estimated_cost = Decimal("0.00")
        task.plan.requires_human_confirmation = intent.intent == "action"

    await session.commit()
    task = await _load_task(session, task.id, user.org_id)
    run = await session.get(AgentRun, agent_run_id)
    if run is not None:
        run.task_id = task.id
        await session.commit()
    if regeneration_source_event_id is None and not user_message_recorded:
        await runtime_graph.record_user_message(
            session,
            task,
            body.message,
            client_message_id=body.client_message_id,
        )
    else:
        if regeneration_source_event_id is None:
            raise RuntimeError("regeneration requested without source event id")
        await runtime_graph.record_regeneration_requested(
            session,
            task,
            source_event_id=regeneration_source_event_id,
            client_message_id=body.client_message_id or uuid4().hex,
        )

    if settings.agent_runtime_async_enabled and not force_inline:
        task.current_focus = "Main Agent is queued for background execution"
        await session.commit()
        await mark_agent_run_queued(
            session,
            agent_run_id,
            task_id=task.id,
            request_payload={
                "operation": "start",
                "task_id": task.id,
                "intent": intent.model_dump(mode="json"),
                "route_decision": route_decision.model_dump(mode="json"),
                "client_message_id": body.client_message_id or uuid4().hex,
            },
        )
        await _submit_durable_agent_run(agent_run_id)
        task = await _load_task(session, task.id, user.org_id)
        return await _runtime_response(session, task, user)

    generation_org_id = user.org_id
    generation_user_id = user.id
    generation_task_id = task.id
    try:
        if body.client_message_id:
            await generation_control.activate(
                generation_org_id,
                generation_user_id,
                body.client_message_id,
            )
        await runtime_graph.start_routed(
            session,
            task,
            route_decision=route_decision,
            intent=intent,
            client_message_id=body.client_message_id,
            agent_run_id=agent_run_id,
            agent_run_attempt=agent_run_attempt,
            execution_owner=execution_owner,
        )
    except asyncio.CancelledError:
        if not body.client_message_id or not await generation_control.is_stop_requested(
            generation_org_id,
            generation_user_id,
            body.client_message_id,
        ):
            raise
        await session.rollback()
        task = await _load_task(session, generation_task_id, generation_org_id)
        await runtime_graph.record_generation_stopped(
            session,
            task,
            client_message_id=body.client_message_id,
        )
    finally:
        if body.client_message_id:
            await generation_control.finish(
                generation_org_id,
                generation_user_id,
                body.client_message_id,
            )
    task = await _load_task(session, generation_task_id, generation_org_id)
    return await _runtime_response(session, task, user)


@router.post(
    "/generations/{client_message_id}/stop",
    response_model=StopBrainGenerationOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def stop_brain_generation(
    client_message_id: str,
    body: StopBrainGenerationRequest,
    user: CurrentUser,
    session: SessionDep,
) -> StopBrainGenerationOut:
    if body.task_id is not None:
        await _load_task_for_user(session, body.task_id, user)
    run = await get_agent_run(
        session,
        org_id=user.org_id,
        requested_by_id=user.id,
        client_message_id=client_message_id,
    )
    if run is not None:
        if run.thread_id is not None and run.turn_id is not None:
            stopped = await request_stop(
                session,
                user=user,
                run_id=run.id,
                reason="Stopped from the legacy generation endpoint.",
            )
            await session.commit()
            await publish_runtime_state_intents(session, stopped.publish_intents)
            try:
                await abort_agent_runtime(run.id)
            except Exception:  # noqa: BLE001 - terminal DB state remains authoritative
                log.warning(
                    "Legacy stop worker abort deferred",
                    extra={"run_id": run.id},
                    exc_info=True,
                )
        else:
            # Historical run rows predate conversation scope and retain the
            # previous cancellation path until their data is retired.
            await request_agent_run_cancel(session, run.id)
            if settings.agent_runtime_async_enabled:
                await abort_agent_runtime(run.id)
    await generation_control.request_stop(user.org_id, user.id, client_message_id)
    return StopBrainGenerationOut(
        client_message_id=client_message_id,
        stop_requested=True,
    )


@router.post(
    "/tasks/{task_id}/regenerate",
    response_model=BrainRuntimeOut,
    status_code=status.HTTP_201_CREATED,
)
async def regenerate_brain_message(
    task_id: int,
    body: RegenerateBrainMessageRequest,
    user: CurrentUser,
    session: SessionDep,
) -> BrainRuntimeOut:
    task = await _load_task_for_user(session, task_id, user)
    events = await runtime_events(session, task.id)
    source_event = next(
        (event for event in reversed(events) if event.type == "brain.runtime.user_message"),
        None,
    )
    if source_event is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前对话没有可重新生成的用户消息",
        )
    source_payload = source_event.payload or {}
    source_message = str(source_payload.get("content") or source_payload.get("message") or "")
    if not source_message.strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="上一条用户消息为空，无法重新生成",
        )

    platforms = list(task.brief.platforms) if task.brief and task.brief.platforms else []
    platform_value = platforms[0] if platforms else Platform.DOUYIN.value
    platform = platform_value if isinstance(platform_value, Platform) else Platform(platform_value)
    account_ids = list(task.brief.account_ids) if task.brief else []
    client_message_id = body.client_message_id or uuid4().hex
    return await _send_brain_message(
        BrainMessageRequest(
            message=source_message,
            client_message_id=client_message_id,
            task_id=task.id,
            project_id=task.brief.project_id if task.brief else None,
            account_id=account_ids[0] if account_ids else None,
            platform=platform,
        ),
        user,
        session,
        regeneration_source_event_id=source_event.id,
    )


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
    visible: list[BrainTask] = []
    for row in rows:
        try:
            await require_task_visibility(session, user, row)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                continue
            raise
        visible.append(row)
    return [BrainTaskOut.model_validate(row) for row in visible]


@router.get("/tasks/{task_id}", response_model=BrainTaskOut)
async def get_task(task_id: int, user: CurrentUser, session: SessionDep) -> BrainTaskOut:
    return BrainTaskOut.model_validate(await _load_task_for_user(session, task_id, user))


@router.get("/tasks/{task_id}/runtime", response_model=BrainRuntimeOut)
async def get_task_runtime(task_id: int, user: CurrentUser, session: SessionDep) -> BrainRuntimeOut:
    task = await _load_task_for_user(session, task_id, user)
    return await _runtime_response(session, task, user)


@router.get("/tasks/{task_id}/strategy", response_model=StrategyPlanOut | None)
async def get_task_strategy(
    task_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> StrategyPlanOut | None:
    task = await _load_task_for_user(session, task_id, user)
    row = await session.scalar(
        select(StrategyPlan)
        .where(
            StrategyPlan.task_id == task.id,
            StrategyPlan.org_id == task.org_id,
        )
        .order_by(StrategyPlan.version.desc(), StrategyPlan.id.desc())
        .limit(1)
    )
    return StrategyPlanOut.model_validate(row) if row is not None else None


@router.get("/tasks/{task_id}/decisions", response_model=list[DecisionTraceOut])
async def list_task_decisions(
    task_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> list[DecisionTraceOut]:
    task = await _load_task_for_user(session, task_id, user)
    rows = (
        await session.scalars(
            select(DecisionTrace)
            .where(
                DecisionTrace.task_id == task.id,
                DecisionTrace.org_id == task.org_id,
            )
            .order_by(DecisionTrace.id)
        )
    ).all()
    return [DecisionTraceOut.model_validate(row) for row in rows]


@router.get(
    "/tasks/{task_id}/quality-scores",
    response_model=list[AgentQualityScoreOut],
)
async def list_task_quality_scores(
    task_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> list[AgentQualityScoreOut]:
    task = await _load_task_for_user(session, task_id, user)
    rows = (
        await session.scalars(
            select(AgentQualityScore)
            .where(
                AgentQualityScore.task_id == task.id,
                AgentQualityScore.org_id == task.org_id,
            )
            .order_by(AgentQualityScore.id)
        )
    ).all()
    return [AgentQualityScoreOut.model_validate(row) for row in rows]


@router.get("/tasks/{task_id}/reflection", response_model=ReflectionRecordOut | None)
async def get_task_reflection(
    task_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ReflectionRecordOut | None:
    task = await _load_task_for_user(session, task_id, user)
    row = await session.scalar(
        select(ReflectionRecord)
        .where(
            ReflectionRecord.task_id == task.id,
            ReflectionRecord.org_id == task.org_id,
        )
        .order_by(ReflectionRecord.id.desc())
        .limit(1)
    )
    return ReflectionRecordOut.model_validate(row) if row is not None else None


@router.post(
    "/tasks/{task_id}/observation/refresh",
    response_model=ReflectionRecordOut,
)
@router.post(
    "/tasks/{task_id}/resume-observation",
    response_model=ReflectionRecordOut,
)
async def refresh_task_observation(
    task_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> ReflectionRecordOut:
    task = await _load_task_for_user(session, task_id, user)
    try:
        reflection = await runtime_graph.refresh_observation(session, task)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return ReflectionRecordOut.model_validate(reflection)


@router.get(
    "/tasks/{task_id}/experience-memories",
    response_model=list[ExperienceMemoryOut],
)
async def get_task_experience_memories(
    task_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> list[ExperienceMemoryOut]:
    task = await _load_task_for_user(session, task_id, user)
    rows = (
        await session.scalars(
            select(ExperienceMemory)
            .where(
                ExperienceMemory.task_id == task.id,
                ExperienceMemory.org_id == task.org_id,
            )
            .order_by(ExperienceMemory.id)
        )
    ).all()
    return [ExperienceMemoryOut.model_validate(row) for row in rows]


@router.post(
    "/tasks/{task_id}/experience-candidates/{candidate_key}/verify",
    response_model=ExperienceMemoryOut,
)
async def verify_task_experience_candidate(
    task_id: int,
    candidate_key: str,
    body: ExperienceVerificationRequest,
    admin: AdminUser,
    session: SessionDep,
) -> ExperienceMemoryOut:
    if body.candidate_key != candidate_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="候选经验标识不一致",
        )
    task = await _load_task_for_user(session, task_id, admin)
    reflection = await session.scalar(
        select(ReflectionRecord)
        .where(
            ReflectionRecord.task_id == task.id,
            ReflectionRecord.org_id == task.org_id,
        )
        .order_by(ReflectionRecord.id.desc())
        .limit(1)
    )
    if reflection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务尚未形成可核验的复盘记录",
        )
    try:
        memory = await ai_coo_learning_service.verify_experience_candidate(
            session,
            reflection=reflection,
            candidate_key=candidate_key,
            verified_by_id=admin.id,
            verification_note=body.verification_note,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return ExperienceMemoryOut.model_validate(memory)


@router.get(
    "/tasks/{task_id}/operation-intelligence",
    response_model=OperationIntelligenceOut,
)
async def get_task_operation_intelligence(
    task_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> OperationIntelligenceOut:
    task = await _load_task_for_user(session, task_id, user)
    return await ai_coo_learning_service.operation_intelligence(
        session,
        task=task,
    )


async def _runtime_response(
    session: AsyncSession,
    task: BrainTask,
    viewer,
) -> BrainRuntimeOut:
    # Runtime locking may refresh the identity-mapped task without relationship
    # loader options. Re-apply the response loader contract here so Pydantic
    # never triggers implicit async IO while extracting ORM attributes.
    loaded_task = await session.scalar(
        select(BrainTask)
        .options(
            selectinload(BrainTask.brief),
            selectinload(BrainTask.plan),
            selectinload(BrainTask.invocations),
            selectinload(BrainTask.acceptances),
        )
        .where(BrainTask.id == task.id, BrainTask.org_id == task.org_id)
    )
    if loaded_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="运营大脑任务不存在",
        )
    task = loaded_task
    tool_calls = (
        await session.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.task_id == task.id, AgentToolCall.org_id == task.org_id)
            .order_by(AgentToolCall.id)
        )
    ).all()
    pending_permissions = [
        row
        for row in tool_calls
        if row.requires_human_confirmation and row.status == "waiting_approval"
    ]
    events = await runtime_events(session, task.id)
    intent = _runtime_intent(events)
    pending_decisions = _pending_runtime_decisions(events)
    strategy = await session.scalar(
        select(StrategyPlan)
        .where(
            StrategyPlan.task_id == task.id,
            StrategyPlan.org_id == task.org_id,
        )
        .order_by(StrategyPlan.version.desc(), StrategyPlan.id.desc())
        .limit(1)
    )
    decisions = (
        await session.scalars(
            select(DecisionTrace)
            .where(
                DecisionTrace.task_id == task.id,
                DecisionTrace.org_id == task.org_id,
            )
            .order_by(DecisionTrace.id)
        )
    ).all()
    quality_scores = (
        await session.scalars(
            select(AgentQualityScore)
            .where(
                AgentQualityScore.task_id == task.id,
                AgentQualityScore.org_id == task.org_id,
            )
            .order_by(AgentQualityScore.id)
        )
    ).all()
    reflection = await session.scalar(
        select(ReflectionRecord)
        .where(
            ReflectionRecord.task_id == task.id,
            ReflectionRecord.org_id == task.org_id,
        )
        .order_by(ReflectionRecord.id.desc())
        .limit(1)
    )
    experience_memories = (
        await session.scalars(
            select(ExperienceMemory)
            .where(
                ExperienceMemory.task_id == task.id,
                ExperienceMemory.org_id == task.org_id,
            )
            .order_by(ExperienceMemory.id)
        )
    ).all()
    operation_intelligence = await ai_coo_learning_service.operation_intelligence(
        session,
        task=task,
    )
    llm_calls: Sequence[LLMCall] = ()
    if viewer.role == UserRole.ADMIN:
        llm_calls = (
            await session.scalars(
                select(LLMCall)
                .where(
                    LLMCall.task_id == task.id,
                    LLMCall.org_id == task.org_id,
                )
                .order_by(LLMCall.id)
            )
        ).all()
    return BrainRuntimeOut(
        task=BrainTaskOut.model_validate(task),
        thread_id=task.thread_id,
        status=await runtime_status(session, task),
        timeline=[RuntimeEventOut.model_validate(row) for row in events],
        invocations=[AgentInvocationOut.model_validate(row) for row in task.invocations],
        tool_calls=[AgentToolCallOut.model_validate(row) for row in tool_calls],
        acceptances=[DeliverableAcceptanceOut.model_validate(row) for row in task.acceptances],
        pending_permissions=[AgentToolCallOut.model_validate(row) for row in pending_permissions],
        intent=intent,
        pending_decisions=pending_decisions,
        strategy=StrategyPlanOut.model_validate(strategy) if strategy is not None else None,
        decisions=[DecisionTraceOut.model_validate(row) for row in decisions],
        quality_scores=[AgentQualityScoreOut.model_validate(row) for row in quality_scores],
        reflection=(
            ReflectionRecordOut.model_validate(reflection)
            if reflection is not None
            else None
        ),
        experience_memories=[
            ExperienceMemoryOut.model_validate(row) for row in experience_memories
        ],
        operation_intelligence=operation_intelligence,
        llm_calls=[LLMCallAuditOut.model_validate(row) for row in llm_calls],
        next_actions=await next_actions(session, task),
    )


def _runtime_intent(events: list) -> IntentDecision | None:
    for event in reversed(events):
        if event.type == "brain.runtime.intent_classified":
            payload = event.payload or {}
            raw = payload.get("intent")
            if isinstance(raw, dict):
                return IntentDecision.model_validate(raw)
    return None


def _pending_runtime_decisions(events: list) -> list[DecisionRequest]:
    pending: dict[str, DecisionRequest] = {}
    for event in events:
        payload = event.payload or {}
        if event.type == "brain.runtime.decision_requested" and isinstance(
            payload.get("decision"), dict
        ):
            decision = DecisionRequest.model_validate(payload["decision"])
            pending[decision.id] = decision
        elif event.type in {
            "brain.runtime.decision_selected",
            "brain.runtime.decision_revised",
        }:
            decision_id = str(payload.get("decision_id") or "")
            pending.pop(decision_id, None)
    return list(pending.values())


@router.post(
    "/tasks/{task_id}/decisions/{decision_id}/select",
    response_model=BrainRuntimeOut,
)
async def select_brain_decision(
    task_id: int,
    decision_id: str,
    body: DecisionSelectionRequest,
    user: CurrentUser,
    session: SessionDep,
) -> BrainRuntimeOut:
    task = await _load_task_for_user(session, task_id, user)
    events = await runtime_events(session, task.id)
    decision = next(
        (row for row in _pending_runtime_decisions(events) if row.id == decision_id),
        None,
    )
    if decision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="待选择方案不存在")
    choice = next((row for row in decision.choices if row.id == body.choice_id), None)
    if choice is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="所选方案不存在")
    if settings.agent_runtime_async_enabled:
        await runtime_graph.record_decision_selected(
            session,
            task,
            decision_id=decision.id,
            choice_id=choice.id,
            choice_title=choice.title,
        )
        task.status = BrainTaskStatus.RUNNING
        task.current_focus = "运营大脑正在根据你的选择恢复执行"
        await session.commit()
        await _queue_runtime_resume(
            session,
            user=user,
            task=task,
            idempotency_key=f"decision:{task.id}:{decision.id}",
            request_payload={
                "operation": "resume_decision",
                "task_id": task.id,
                "decision_id": decision.id,
                "choice_id": choice.id,
                "choice_title": choice.title,
            },
        )
    else:
        await runtime_graph.resume_after_decision(
            session,
            task,
            decision_id=decision.id,
            choice_id=choice.id,
            choice_title=choice.title,
        )
    return await _runtime_response(
        session,
        await _load_task(session, task.id, user.org_id),
        user,
    )


@router.post(
    "/tasks/{task_id}/decisions/{decision_id}/revise",
    response_model=BrainRuntimeOut,
)
async def revise_brain_decision(
    task_id: int,
    decision_id: str,
    body: DecisionRevisionRequest,
    user: CurrentUser,
    session: SessionDep,
) -> BrainRuntimeOut:
    task = await _load_task_for_user(session, task_id, user)
    events = await runtime_events(session, task.id)
    decision = next(
        (row for row in _pending_runtime_decisions(events) if row.id == decision_id),
        None,
    )
    if decision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="待修改方案不存在")
    await runtime_graph.revise_decision(
        session,
        task,
        decision=decision,
        comment=body.comment,
        request_new_options=body.request_new_options,
    )
    return await _runtime_response(
        session,
        await _load_task(session, task.id, user.org_id),
        user,
    )


@router.post("/tasks/{task_id}/confirm", response_model=BrainTaskOut)
async def confirm_task(task_id: int, user: CurrentUser, session: SessionDep) -> BrainTaskOut:
    task = await _load_task_for_user(session, task_id, user)
    if task.brief and _is_casual_goal(task.brief.goal):
        await runtime_graph.start_casual_turn(session, task)
    else:
        await runtime_graph.start(session, task)
    return BrainTaskOut.model_validate(await _load_task(session, task_id, user.org_id))


@router.get("/tasks/{task_id}/invocations", response_model=list[AgentInvocationOut])
async def list_invocations(
    task_id: int, user: CurrentUser, session: SessionDep
) -> list[AgentInvocationOut]:
    task = await _load_task_for_user(session, task_id, user)
    return [AgentInvocationOut.model_validate(row) for row in task.invocations]


@router.get("/tasks/{task_id}/tool-calls", response_model=list[AgentToolCallOut])
async def list_tool_calls(
    task_id: int, user: CurrentUser, session: SessionDep
) -> list[AgentToolCallOut]:
    await _load_task_for_user(session, task_id, user)
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
    visible: list[AgentToolCall] = []
    for row in rows:
        task = await session.get(BrainTask, row.task_id)
        if task is None:
            continue
        try:
            await require_task_visibility(session, user, task)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                continue
            raise
        visible.append(row)
    return [AgentToolCallOut.model_validate(row) for row in visible]


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


async def _audit_task_approval_decision(
    session: AsyncSession,
    *,
    user: CurrentUser,
    task: BrainTask,
    approval_kind: str,
    source_id: int,
    title: str,
    approved: bool,
    comment: str | None,
) -> None:
    project_ids = await task_project_ids(session, task)
    audit_project_ids: list[int | None] = list(sorted(project_ids))
    if not audit_project_ids:
        audit_project_ids = [None]
    for project_id in audit_project_ids:
        await add_approval_decided(
            session,
            org_id=user.org_id,
            project_id=project_id,
            content_item_id=task.content_item_id,
            approval_kind=approval_kind,
            source_id=source_id,
            title=title,
            approved=approved,
            actor_user_id=user.id,
            comment=comment,
        )


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该工具调用不需要人工确认",
        )
    task = await _load_task(session, tool_call.task_id, user.org_id)
    await require_task_approval_access(session, user, task)
    scoped_skill = (
        await session.get(SkillRun, tool_call.skill_run_id)
        if tool_call.skill_run_id is not None
        else None
    )
    scoped_run = (
        await session.get(AgentRun, scoped_skill.run_id)
        if scoped_skill is not None
        else None
    )
    if (
        scoped_skill is not None
        and scoped_run is not None
        and scoped_run.thread_id is not None
        and scoped_run.turn_id is not None
    ):
        finish_lock = await lock_composite_finish_approval(
            session,
            tool_call=tool_call,
        )
        requested = await request_interrupt(
            session,
            user=user,
            run_id=scoped_run.id,
            kind="approval",
            semantic_key=f"tool-approval:{tool_call.id}",
            public_message=f"Confirm {tool_call.tool_name}.",
            action_label="Confirm action",
            response_schema={
                "type": "object",
                "required": ["approved"],
                "properties": {"approved": {"type": "boolean"}},
            },
            skill_run_id=scoped_skill.id,
            source_type="tool_call",
            source_id=tool_call.id,
            source_version=1,
            prelocked=finish_lock.runtime_lock,
        )
        resolved = await resolve_interrupt(
            session,
            user=user,
            interrupt_id=requested.interrupt.id,
            expected_version=requested.interrupt.version,
            idempotency_key=f"legacy-tool-approval:{tool_call.id}",
            resolution={
                "approved": body.approved,
                "comment": body.comment or "",
            },
            prelocked=finish_lock.runtime_lock,
        )
        await session.commit()
        await publish_runtime_state_intents(
            session,
            (*requested.publish_intents, *resolved.publish_intents),
        )
        if resolved.replay_runtime_events:
            await replay_runtime_state_events(session, run_id=scoped_run.id)
        if resolved.dispatch_intent is not None:
            try:
                await enqueue_agent_runtime(run_id=resolved.dispatch_intent.run_id)
            except Exception:  # noqa: BLE001 - queued DB state is the durable outbox
                log.warning(
                    "Legacy approval resume dispatch deferred",
                    extra={"run_id": scoped_run.id},
                    exc_info=True,
                )
        await session.refresh(tool_call)
        return AgentToolCallOut.model_validate(tool_call)

    # Historical unscoped approval rows retain the previous path while current
    # conversation-scoped executions use TurnInterrupt as their sole truth.
    finish_lock = await lock_composite_finish_approval(session, tool_call=tool_call)
    tool_call = finish_lock.tool_call
    if tool_call.status != "waiting_approval":
        prior_decision = dict(tool_call.meta or {}).get("decision")
        if (
            isinstance(prior_decision, dict)
            and prior_decision.get("approved") is body.approved
            and tool_call.skill_run_id is not None
        ):
            decided_skill = await session.get(SkillRun, tool_call.skill_run_id)
            if decided_skill is not None:
                await replay_runtime_state_events(session, run_id=decided_skill.run_id)
            return AgentToolCallOut.model_validate(tool_call)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该工具调用已经完成审批",
        )

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
        await sync_publish_jobs_after_approval(
            session,
            org_id=user.org_id,
            tool_call=tool_call,
            approved=body.approved,
        )
    await _audit_task_approval_decision(
        session,
        user=user,
        task=task,
        approval_kind="tool_call",
        source_id=tool_call.id,
        title=str(next_meta.get("content_title") or tool_call.tool_name),
        approved=body.approved,
        comment=body.comment,
    )
    try:
        skill_finish_result = await finalize_skill_finish_approval(
            session,
            tool_call=tool_call,
            task=task,
            approved=body.approved,
            comment=body.comment,
            prelocked=finish_lock.runtime_lock,
        )
    except SkillApprovalConflict as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    await session.commit()
    await publish_runtime_state_intents(session, skill_finish_result.publish_intents)
    await session.refresh(tool_call)
    if skill_finish_result.handled:
        return AgentToolCallOut.model_validate(tool_call)
    remaining_permission = await session.scalar(
        select(AgentToolCall.id).where(
            AgentToolCall.task_id == task.id,
            AgentToolCall.org_id == user.org_id,
            AgentToolCall.requires_human_confirmation.is_(True),
            AgentToolCall.status == "waiting_approval",
        )
    )
    if settings.agent_runtime_async_enabled:
        if remaining_permission is None:
            task.status = BrainTaskStatus.RUNNING
            task.current_focus = "运营大脑正在恢复受控任务"
            await session.commit()
            await _queue_runtime_resume(
                session,
                user=user,
                task=task,
                idempotency_key=f"permission:{task.id}:{tool_call.id}",
                request_payload={
                    "operation": "resume_permission",
                    "task_id": task.id,
                    "tool_call_id": tool_call.id,
                    "approved": body.approved,
                },
            )
    else:
        await runtime_graph.resume_after_permission(session, task, tool_call, body.approved)
    await session.refresh(tool_call)
    return AgentToolCallOut.model_validate(tool_call)


@router.get("/tasks/{task_id}/acceptances", response_model=list[DeliverableAcceptanceOut])
async def list_acceptances(
    task_id: int, user: CurrentUser, session: SessionDep
) -> list[DeliverableAcceptanceOut]:
    task = await _load_task_for_user(session, task_id, user)
    return [DeliverableAcceptanceOut.model_validate(row) for row in task.acceptances]


@router.post("/tasks/{task_id}/accept", response_model=DeliverableAcceptanceOut)
async def accept_deliverable(
    task_id: int, body: AcceptDeliverableRequest, user: CurrentUser, session: SessionDep
) -> DeliverableAcceptanceOut:
    acceptance = await _load_acceptance(session, task_id, user.org_id, body.acceptance_id)
    task = await _load_task(session, task_id, user.org_id)
    await require_task_approval_access(session, user, task)
    acceptance.status = DeliverableAcceptanceStatus.APPROVED
    acceptance.reviewer_note = body.reviewer_note or "用户已确认通过。"
    acceptance.rerun_scope = None
    if task.acceptances and all(
        row.status == DeliverableAcceptanceStatus.APPROVED for row in task.acceptances
    ):
        task.status = BrainTaskStatus.COMPLETED
        task.progress = 100
        task.current_focus = "等待用户关闭本次任务记忆"
    await _audit_task_approval_decision(
        session,
        user=user,
        task=task,
        approval_kind="deliverable",
        source_id=acceptance.id,
        title=acceptance.title,
        approved=True,
        comment=body.reviewer_note,
    )
    await session.commit()
    await session.refresh(acceptance)
    return DeliverableAcceptanceOut.model_validate(acceptance)


@router.post("/tasks/{task_id}/rerun", response_model=DeliverableAcceptanceOut)
async def rerun_deliverable(
    task_id: int, body: RerunDeliverableRequest, user: CurrentUser, session: SessionDep
) -> DeliverableAcceptanceOut:
    acceptance = await _load_acceptance(session, task_id, user.org_id, body.acceptance_id)
    task = await _load_task(session, task_id, user.org_id)
    await require_task_approval_access(session, user, task)
    acceptance = await rerun_brain_acceptance(
        session, task, acceptance, body.rerun_scope, body.reason
    )
    if body.ask_brain_rejudge:
        acceptance.brain_rejudge_summary = (
            acceptance.brain_rejudge_summary or "运营大脑建议保留已通过交付物，仅重跑受影响范围。"
        )
    await _audit_task_approval_decision(
        session,
        user=user,
        task=task,
        approval_kind="deliverable",
        source_id=acceptance.id,
        title=acceptance.title,
        approved=False,
        comment=body.reason,
    )
    await session.commit()
    await session.refresh(acceptance)
    return DeliverableAcceptanceOut.model_validate(acceptance)


@router.post("/tasks/{task_id}/rejudge", response_model=DeliverableAcceptanceOut)
async def rejudge_deliverable(
    task_id: int, body: RejudgeDeliverableRequest, user: CurrentUser, session: SessionDep
) -> DeliverableAcceptanceOut:
    acceptance = await _load_acceptance(session, task_id, user.org_id, body.acceptance_id)
    task = await _load_task(session, task_id, user.org_id)
    await require_task_approval_access(session, user, task)
    acceptance.status = DeliverableAcceptanceStatus.RERUN_REQUESTED
    acceptance.rerun_scope = acceptance.rerun_scope or RerunScope.CURRENT_AGENT
    await _audit_task_approval_decision(
        session,
        user=user,
        task=task,
        approval_kind="deliverable",
        source_id=acceptance.id,
        title=acceptance.title,
        approved=False,
        comment="运营大脑重新判断该成果。",
    )
    acceptance.brain_rejudge_summary = "运营大脑重新判断：问题集中在当前交付物，不建议全链重跑。"
    acceptance.brain_rejudge_basis = [
        "问题未影响已通过的上游定位。",
        "下游依赖可在当前交付物更新后再同步刷新。",
    ]
    await session.commit()
    await session.refresh(acceptance)
    return DeliverableAcceptanceOut.model_validate(acceptance)


@router.post("/tasks/{task_id}/close-memory", response_model=CloseMemoryOut)
async def close_memory(task_id: int, user: CurrentUser, session: SessionDep) -> CloseMemoryOut:
    task = await _load_task_for_user(session, task_id, user)
    if task.status != BrainTaskStatus.COMPLETED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="任务尚未最终验收")
    task.context_closed_at = datetime.now(UTC)
    await session.commit()
    return CloseMemoryOut(task_id=task.id, closed=True, context_closed_at=task.context_closed_at)
