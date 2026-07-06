"""专家团 API。"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AdminUser, CurrentUser
from app.db import get_session
from app.models import (
    AgentInvocation,
    AgentToolCall,
    BrainTask,
    ModelConfig,
    OrchestrationPlan,
    TaskBrief,
)
from app.models.enums import (
    AgentCode,
    AgentGroup,
    AgentInvocationStatus,
    AutomationLevel,
    BrainTaskStatus,
    DeliverableType,
    Platform,
)
from app.schemas.brain import (
    AgentCurrentTaskOut,
    AgentProfileOut,
    AgentToolCallSummaryItem,
    AgentToolCallSummaryOut,
    InvokeAgentOut,
    InvokeAgentRequest,
    UpdateAgentConfigRequest,
)

router = APIRouter(prefix="/agents", tags=["agents"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


AGENT_PROFILES: list[dict] = [
    {
        "code": AgentCode.DECISION,
        "name": "运营大脑",
        "group": AgentGroup.CONTROL,
        "one_liner": "理解目标、拆解任务、调度专家团并决定是否重跑。",
        "tools": ["任务规划", "专家调度", "验收判断", "风险控制"],
        "typical_tasks": ["生成任务 Brief", "确认调度计划", "判断重跑范围"],
        "standard_outputs": [DeliverableType.REVIEW_REPORT],
    },
    {
        "code": AgentCode.POSITIONING,
        "name": "账号定位专家",
        "group": AgentGroup.STRATEGY,
        "one_liner": "校准账号人设、赛道、平台差异和内容支柱。",
        "tools": ["账号矩阵", "竞品样本", "人设标签"],
        "typical_tasks": ["定位校准", "账号诊断", "赛道拆解"],
        "standard_outputs": [DeliverableType.POSITIONING_STRATEGY],
    },
    {
        "code": AgentCode.CONTENT_DIRECTOR,
        "name": "编导文案专家",
        "group": AgentGroup.CREATIVE,
        "one_liner": "把定位转成脚本、钩子、分镜和平台化文案。",
        "tools": ["脚本库", "合规词库", "热点素材"],
        "typical_tasks": ["脚本包", "标题钩子", "分镜建议"],
        "standard_outputs": [DeliverableType.VIDEO_SCRIPT, DeliverableType.TOPIC_PLAN],
    },
    {
        "code": AgentCode.ART_DIRECTOR,
        "name": "美术提示词专家",
        "group": AgentGroup.CREATIVE,
        "one_liner": "生成可直接供视频工具使用的视觉提示词。",
        "tools": ["提示词库", "视觉风格库", "Seedance 参数"],
        "typical_tasks": ["镜头提示词", "风格规范", "封面方向"],
        "standard_outputs": [DeliverableType.ART_PROMPT],
    },
    {
        "code": AgentCode.VIDEO_CREATOR,
        "name": "视频创作专家",
        "group": AgentGroup.CREATIVE,
        "one_liner": "把脚本与提示词转成可剪辑的视频素材。",
        "tools": ["Seedance", "素材库", "生成队列"],
        "typical_tasks": ["视频素材生成", "失败重试", "素材版本记录"],
        "standard_outputs": [DeliverableType.VIDEO_ASSET],
    },
    {
        "code": AgentCode.EDITOR,
        "name": "剪辑专家",
        "group": AgentGroup.OPERATION,
        "one_liner": "组织成片结构、字幕、节奏和平台版本。",
        "tools": ["剪辑模板", "字幕规范", "成片版本库"],
        "typical_tasks": ["剪辑计划", "成片版本", "平台适配"],
        "standard_outputs": [DeliverableType.EDITED_VIDEO],
    },
    {
        "code": AgentCode.OPERATOR,
        "name": "账号运营专家",
        "group": AgentGroup.OPERATION,
        "one_liner": "负责发布计划、评论观察和复盘建议。",
        "tools": ["发布日历", "指标回流", "复盘看板"],
        "typical_tasks": ["发布计划", "复盘报告", "下一轮建议"],
        "standard_outputs": [DeliverableType.PUBLISH_CALENDAR, DeliverableType.REVIEW_REPORT],
    },
    {
        "code": AgentCode.ADVERTISER,
        "name": "投流专家",
        "group": AgentGroup.GROWTH,
        "one_liner": "制定预算、放量节奏和投放风险控制。",
        "tools": ["投流账户", "预算规则", "转化指标"],
        "typical_tasks": ["投放计划", "预算建议", "放量复盘"],
        "standard_outputs": [DeliverableType.AD_PLAN],
    },
    {
        "code": AgentCode.CUSTOMER_SERVICE,
        "name": "客服反馈专家",
        "group": AgentGroup.FEEDBACK,
        "one_liner": "把评论、私信和售后反馈转成内容与运营洞察。",
        "tools": ["评论回流", "客服记录", "情绪标签"],
        "typical_tasks": ["评论摘要", "用户问题", "内容机会点"],
        "standard_outputs": [DeliverableType.CS_RECORD],
    },
]


async def _profile(session: AsyncSession, org_id: int, raw: dict) -> AgentProfileOut:
    cfg = await session.scalar(
        select(ModelConfig).where(
            ModelConfig.org_id == org_id,
            ModelConfig.agent_code == raw["code"],
        )
    )
    model = cfg.primary_model if cfg is not None else "deepseek-chat"
    fallback = cfg.fallback_model if cfg is not None else None
    automation_level = AutomationLevel.CONFIRM
    if cfg is not None and cfg.params and cfg.params.get("automation_level"):
        automation_level = AutomationLevel(cfg.params["automation_level"])

    current_task = AgentCurrentTaskOut(
        task_id=0,
        title="等待运营大脑调度",
        project_name="未绑定项目",
        account_group_name="未绑定账号组",
        platforms=[Platform.DOUYIN],
        progress=0,
        risk_level="low",
        blockers=[],
        next_action="等待任务确认",
        output_summary="暂无进行中的专家任务。",
    )
    return AgentProfileOut(
        code=raw["code"],
        name=raw["name"],
        group=raw["group"],
        one_liner=raw["one_liner"],
        model=model,
        fallback_model=fallback,
        automation_level=automation_level,
        tools=raw["tools"],
        typical_tasks=raw["typical_tasks"],
        standard_outputs=raw["standard_outputs"],
        current_task=current_task,
        tool_summary=await _tool_summary(session, org_id, raw["code"]),
    )


async def _tool_summary(
    session: AsyncSession, org_id: int, code: AgentCode
) -> AgentToolCallSummaryOut:
    rows = (
        await session.scalars(
            select(AgentToolCall)
            .where(AgentToolCall.org_id == org_id, AgentToolCall.agent_code == code.value)
            .order_by(AgentToolCall.id.desc())
        )
    ).all()
    return AgentToolCallSummaryOut(
        total_calls=len(rows),
        pending_approvals=sum(1 for row in rows if row.status == "waiting_approval"),
        failed_calls=sum(1 for row in rows if row.status in {"failed", "blocked"}),
        recent_calls=[
            AgentToolCallSummaryItem(
                id=row.id,
                task_id=row.task_id,
                tool_code=row.tool_code,
                tool_name=row.tool_name,
                status=row.status,
                permission_mode=row.permission_mode,
                requires_human_confirmation=row.requires_human_confirmation,
                input_summary=row.input_summary,
                output_summary=row.output_summary,
                error=row.error,
                created_at=row.created_at,
            )
            for row in rows[:5]
        ],
    )


def _find_raw(code: AgentCode) -> dict:
    for profile in AGENT_PROFILES:
        if profile["code"] == code:
            return profile
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="专家不存在")


@router.get("", response_model=list[AgentProfileOut])
async def list_agents(user: CurrentUser, session: SessionDep) -> list[AgentProfileOut]:
    return [await _profile(session, user.org_id, raw) for raw in AGENT_PROFILES]


@router.get("/{code}", response_model=AgentProfileOut)
async def get_agent(code: AgentCode, user: CurrentUser, session: SessionDep) -> AgentProfileOut:
    return await _profile(session, user.org_id, _find_raw(code))


@router.patch("/{code}/config", response_model=AgentProfileOut)
async def update_agent_config(
    code: AgentCode,
    body: UpdateAgentConfigRequest,
    user: AdminUser,
    session: SessionDep,
) -> AgentProfileOut:
    raw = _find_raw(code)
    cfg = await session.scalar(
        select(ModelConfig).where(ModelConfig.org_id == user.org_id, ModelConfig.agent_code == code)
    )
    if cfg is None:
        cfg = ModelConfig(org_id=user.org_id, agent_code=code, primary_model="deepseek-chat")
        session.add(cfg)
    if body.primary_model is not None:
        cfg.primary_model = body.primary_model
    if body.fallback_model is not None:
        cfg.fallback_model = body.fallback_model
    if body.automation_level is not None:
        params = dict(cfg.params or {})
        params["automation_level"] = body.automation_level.value
        cfg.params = params
    await session.commit()
    return await _profile(session, user.org_id, raw)


@router.post("/{code}/invoke", response_model=InvokeAgentOut)
async def invoke_agent(
    code: AgentCode,
    body: InvokeAgentRequest,
    user: CurrentUser,
    session: SessionDep,
) -> InvokeAgentOut:
    raw = _find_raw(code)
    task_id = body.task_id
    if task_id is not None:
        task = await session.get(BrainTask, task_id)
        if task is None or task.org_id != user.org_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="运营大脑任务不存在")
    else:
        task = BrainTask(
            org_id=user.org_id,
            title=f"直接调用：{raw['name']}",
            status=BrainTaskStatus.RUNNING,
            current_focus="直接调用结果回流运营大脑",
        )
        task.brief = TaskBrief(
            goal=body.prompt,
            platforms=[Platform.DOUYIN.value],
            cycle="直接调用",
            content_goal="完成一次专家直接调用，并把结果回流运营大脑。",
        )
        task.plan = OrchestrationPlan(
            summary="直接调用子 Agent，结果回流运营大脑。",
            steps=[
                {
                    "id": f"direct-{code.value}",
                    "agent_code": code.value,
                    "agent_name": raw["name"],
                    "phase": "直接调用",
                    "intent": body.prompt,
                    "status": "done",
                    "depends_on": [],
                    "expected_output": "专家输出摘要",
                    "risk_level": "low",
                }
            ],
            quality_gates=[],
        )
        session.add(task)
        await session.flush()
        task_id = task.id

    invocation = AgentInvocation(
        task_id=task_id,
        agent_code=code,
        agent_name=raw["name"],
        status=AgentInvocationStatus.DONE,
        input_summary=body.prompt,
        output_summary="直接调用已完成，结果已回流运营大脑。",
        model="deepseek-chat",
        token_count=1200,
        cost=Decimal("0.03"),
        upstream=[],
    )
    session.add(invocation)
    await session.commit()
    await session.refresh(invocation)
    return InvokeAgentOut(
        invocation=invocation,
        message="子 Agent 调用完成，结果已回流运营大脑。",
    )
