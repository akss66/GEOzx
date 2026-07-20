"""Project-scoped direct expert execution and handoff ledger."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.advertising import AdvertisingAgent
from app.agents.art import ArtAgent
from app.agents.base import AgentContext, BaseAgent
from app.agents.content import ContentAgent
from app.agents.customer_service import CustomerServiceAgent
from app.agents.editing import EditingAgent
from app.agents.operation import OperationAgent
from app.agents.positioning import PositioningAgent
from app.agents.video import VideoAgent
from app.core.approval_audit import add_approval_requested
from app.core.workspace_access import require_account_access, require_project_access
from app.models import (
    AgentInvocation,
    BrainTask,
    ContentItem,
    Deliverable,
    DeliverableAcceptance,
    Event,
    KnowledgeCitation,
    KnowledgeEntry,
    KnowledgeSuggestion,
    ModelConfig,
    OrchestrationPlan,
    ProjectAccount,
    TaskBrief,
    User,
)
from app.models.enums import (
    AccountStatus,
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    BrainTaskType,
    ContentStage,
    ContentStatus,
    DeliverableAcceptanceStatus,
    DeliverableStatus,
    DeliverableType,
    KnowledgeCategory,
    WorkspaceRole,
)
from app.services.agent_management import get_business_config, require_agent_enabled
from app.services.knowledge_workspace import (
    knowledge_context,
    list_agent_knowledge,
    record_knowledge_citations,
)


class AgentExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentSpec:
    name: str
    runner: type[BaseAgent]
    deliverable_type: DeliverableType
    deliverable_title: str
    stage: ContentStage
    task_type: BrainTaskType


@dataclass(frozen=True)
class AgentRunBundle:
    task: BrainTask
    invocation: AgentInvocation
    deliverable: Deliverable
    acceptance: DeliverableAcceptance
    knowledge_sources: list[KnowledgeEntry]


_OPERATING_ROLES = {WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR}

AGENT_SPECS: dict[AgentCode, AgentSpec] = {
    AgentCode.POSITIONING: AgentSpec(
        "账号定位专家",
        PositioningAgent,
        DeliverableType.POSITIONING_STRATEGY,
        "账号定位方案",
        ContentStage.POSITIONING,
        BrainTaskType.ACCOUNT_DIAGNOSIS,
    ),
    AgentCode.CONTENT_DIRECTOR: AgentSpec(
        "编导文案专家",
        ContentAgent,
        DeliverableType.VIDEO_SCRIPT,
        "视频脚本",
        ContentStage.CONTENT_DIRECTION,
        BrainTaskType.CONTENT_CREATION,
    ),
    AgentCode.ART_DIRECTOR: AgentSpec(
        "美术提示词专家",
        ArtAgent,
        DeliverableType.ART_PROMPT,
        "视觉提示方案",
        ContentStage.ART_DIRECTION,
        BrainTaskType.CONTENT_CREATION,
    ),
    AgentCode.VIDEO_CREATOR: AgentSpec(
        "视频创作专家",
        VideoAgent,
        DeliverableType.VIDEO_ASSET,
        "视频素材方案",
        ContentStage.VIDEO_CREATION,
        BrainTaskType.CONTENT_CREATION,
    ),
    AgentCode.EDITOR: AgentSpec(
        "剪辑专家",
        EditingAgent,
        DeliverableType.EDITED_VIDEO,
        "剪辑成片方案",
        ContentStage.EDITING,
        BrainTaskType.CONTENT_CREATION,
    ),
    AgentCode.OPERATOR: AgentSpec(
        "账号运营专家",
        OperationAgent,
        DeliverableType.REVIEW_REPORT,
        "运营复盘报告",
        ContentStage.OPERATION,
        BrainTaskType.REVIEW_OPTIMIZATION,
    ),
    AgentCode.ADVERTISER: AgentSpec(
        "投流专家",
        AdvertisingAgent,
        DeliverableType.AD_PLAN,
        "投流方案",
        ContentStage.ADVERTISING,
        BrainTaskType.REVIEW_OPTIMIZATION,
    ),
    AgentCode.CUSTOMER_SERVICE: AgentSpec(
        "客服反馈专家",
        CustomerServiceAgent,
        DeliverableType.CS_RECORD,
        "用户反馈报告",
        ContentStage.CUSTOMER_SERVICE,
        BrainTaskType.REVIEW_OPTIMIZATION,
    ),
}


async def create_direct_agent_run(
    session: AsyncSession,
    *,
    user: User,
    code: AgentCode,
    project_id: int,
    account_id: int,
    prompt: str,
    source_task_id: int | None = None,
) -> AgentRunBundle:
    spec = _spec(code)
    await require_agent_enabled(session, user.org_id, code)
    management = await get_business_config(
        session,
        user.org_id,
        code,
        responsibility=spec.name,
    )
    project, account = await _require_scope(
        session,
        user=user,
        project_id=project_id,
        account_id=account_id,
        roles=_OPERATING_ROLES,
    )
    upstream = {
        "account_context": {
            "account_id": account.id,
            "nickname": account.nickname,
            "platform": account.platform.value,
            "project_id": project.id,
            "project_name": project.name,
        }
    }
    source_deliverable_id: int | None = None
    if source_task_id is not None:
        source = await _load_direct_task(session, source_task_id, user.org_id)
        _assert_task_scope(source, project_id, account_id)
        source_deliverable = await session.scalar(
            select(Deliverable)
            .where(Deliverable.content_item_id == source.content_item_id)
            .order_by(Deliverable.id.desc())
        )
        if source_deliverable is not None:
            source_deliverable_id = source_deliverable.id
            upstream["previous_result"] = source_deliverable.payload

    content_item = ContentItem(
        project_id=project.id,
        created_by_id=user.id,
        account_id=account.id,
        title=f"{spec.name}：{prompt[:180]}",
        current_stage=spec.stage,
        status=ContentStatus.IN_PROGRESS,
    )
    session.add(content_item)
    await session.flush()

    task = BrainTask(
        org_id=user.org_id,
        created_by_id=user.id,
        content_item_id=content_item.id,
        title=f"直接调用 · {spec.name}",
        type=spec.task_type,
        status=BrainTaskStatus.RUNNING,
        progress=20,
        current_focus=f"{spec.name}正在处理",
        risk_count=0,
        runtime_mode="direct_agent",
        thread_id=f"direct-agent-{uuid4().hex}",
    )
    task.brief = TaskBrief(
        goal=prompt,
        project_id=project.id,
        project_name=project.name,
        platforms=[account.platform.value],
        account_ids=[account.id],
        cycle="独立专家调用",
        content_goal=f"由{spec.name}产出可审阅、可采用的正式成果。",
        expected_outputs=[spec.deliverable_title],
        confirmation_actions=["采用成果", "提出修改", "交回主 Agent"],
    )
    task.plan = OrchestrationPlan(
        summary=f"独立调用{spec.name}，成果进入人工验收后再写入正式工作流。",
        steps=[_plan_step(code, spec, prompt, "running")],
        quality_gates=[*management["quality_gates"], "人工采用成果"],
        requires_human_confirmation=True,
    )
    session.add(task)
    await session.flush()

    invocation = AgentInvocation(
        task_id=task.id,
        agent_code=code,
        agent_name=spec.name,
        status=AgentInvocationStatus.RUNNING,
        input_summary=prompt,
        output_summary="",
        model=await _model_name(session, user.org_id, code),
        token_count=0,
        cost=Decimal("0"),
        upstream=[source_deliverable_id] if source_deliverable_id is not None else [],
        started_at=datetime.now(UTC),
    )
    session.add(invocation)
    session.add(
        Event(
            type="agent.direct.started",
            content_item_id=content_item.id,
            project_id=project.id,
            payload={
                "task_id": task.id,
                "agent_code": code.value,
                "account_id": account.id,
                "source_task_id": source_task_id,
            },
        )
    )
    await session.commit()

    runner = spec.runner()
    runner.code = code.value
    knowledge_rows = (
        await list_agent_knowledge(
            session,
            org_id=user.org_id,
            client_id=project.client_id,
            project_id=project.id,
        )
        if project.client_id is not None
        else []
    )
    try:
        payload = await runner.run(
            session,
            user.org_id,
            AgentContext(
                content_item_id=content_item.id,
                request=prompt,
                upstream=upstream,
                knowledge=knowledge_context(knowledge_rows),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - persist a durable failure ledger
        await session.rollback()
        failed_task = await session.get(BrainTask, task.id)
        failed_invocation = await session.get(AgentInvocation, invocation.id)
        if failed_task is not None:
            failed_task.status = BrainTaskStatus.FAILED
            failed_task.current_focus = "专家执行失败，请检查模型配置后重试"
        if failed_invocation is not None:
            failed_invocation.status = AgentInvocationStatus.FAILED
            failed_invocation.failure_reason = type(exc).__name__
            failed_invocation.finished_at = datetime.now(UTC)
        await session.commit()
        raise AgentExecutionError("专家执行失败") from exc

    payload_dict = payload.model_dump(mode="json")
    summary = _payload_summary(payload_dict)
    deliverable = Deliverable(
        content_item_id=content_item.id,
        agent_code=code.value,
        type=spec.deliverable_type,
        version=1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload=payload_dict,
        note="独立专家调用生成，等待人工采用。",
    )
    session.add(deliverable)
    await session.flush()

    acceptance = DeliverableAcceptance(
        task_id=task.id,
        deliverable_id=deliverable.id,
        agent_code=code,
        agent_name=spec.name,
        deliverable_type=spec.deliverable_type,
        title=spec.deliverable_title,
        version=1,
        summary=summary,
        acceptance_items=_acceptance_items(payload_dict),
        history_versions=[
            {
                "version": 1,
                "status": DeliverableStatus.PENDING_REVIEW.value,
                "note": "等待人工采用",
                "created_at": datetime.now(UTC).isoformat(),
            }
        ],
        status=DeliverableAcceptanceStatus.PENDING,
        brain_rejudge_basis=[
            "该成果来自当前项目和账号上下文。",
            "采用前不会覆盖现有正式成果。",
        ],
    )
    session.add(acceptance)
    await session.flush()
    if project.client_id is not None:
        await record_knowledge_citations(
            session,
            rows=knowledge_rows,
            org_id=user.org_id,
            client_id=project.client_id,
            project_id=project.id,
            task_id=task.id,
            invocation_id=invocation.id,
            agent_code=code.value,
            context=prompt[:500],
        )

    invocation.status = AgentInvocationStatus.DONE
    invocation.output_summary = summary
    invocation.finished_at = datetime.now(UTC)
    task.status = BrainTaskStatus.PENDING_ACCEPTANCE
    task.progress = 90
    task.current_focus = "专家成果等待人工采用"
    task.plan.steps = [_plan_step(code, spec, prompt, "done")]
    session.add(
        Event(
            type="agent.direct.completed",
            content_item_id=content_item.id,
            project_id=project.id,
            payload={
                "task_id": task.id,
                "agent_code": code.value,
                "account_id": account.id,
                "deliverable_id": deliverable.id,
                "acceptance_id": acceptance.id,
            },
        )
    )
    await add_approval_requested(
        session,
        org_id=user.org_id,
        project_id=project.id,
        content_item_id=content_item.id,
        approval_kind="deliverable",
        source_id=acceptance.id,
        title=acceptance.title,
        body=f"{spec.name}已完成独立调用，请确认是否采用。",
    )
    await session.commit()
    return await load_agent_run(session, task.id, user.org_id)


async def list_agent_runs(
    session: AsyncSession,
    *,
    user: User,
    code: AgentCode,
    project_id: int,
    account_id: int,
) -> list[AgentRunBundle]:
    _spec(code)
    await _require_scope(
        session,
        user=user,
        project_id=project_id,
        account_id=account_id,
        roles=None,
    )
    tasks = (
        await session.scalars(
            select(BrainTask)
            .options(selectinload(BrainTask.brief), selectinload(BrainTask.plan))
            .where(BrainTask.org_id == user.org_id, BrainTask.runtime_mode == "direct_agent")
            .order_by(BrainTask.id.desc())
        )
    ).all()
    scoped = [
        task
        for task in tasks
        if task.brief is not None
        and task.brief.project_id == project_id
        and account_id in task.brief.account_ids
    ]
    bundles: list[AgentRunBundle] = []
    for task in scoped:
        invocation = await session.scalar(
            select(AgentInvocation).where(
                AgentInvocation.task_id == task.id,
                AgentInvocation.agent_code == code,
                AgentInvocation.status == AgentInvocationStatus.DONE,
            )
        )
        if invocation is not None:
            bundles.append(await load_agent_run(session, task.id, user.org_id))
    return bundles


async def handoff_agent_run(
    session: AsyncSession,
    *,
    user: User,
    task_id: int,
) -> tuple[BrainTask, str]:
    bundle = await load_agent_run(session, task_id, user.org_id)
    project_id = bundle.task.brief.project_id
    account_id = bundle.task.brief.account_ids[0] if bundle.task.brief.account_ids else None
    if project_id is None or account_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="专家任务缺少账号上下文")
    await _require_scope(
        session,
        user=user,
        project_id=project_id,
        account_id=account_id,
        roles=None,
    )
    prompt = (
        f"请接续专家任务 #{bundle.task.id} 的成果，并为当前账号安排下一步工作。\n"
        f"专家：{bundle.invocation.agent_name}\n"
        f"用户原始目标：{bundle.task.brief.goal}\n"
        f"专家结论：{bundle.acceptance.summary}\n"
        "请先说明你准备调用哪些专家；遇到发布或外部平台动作时必须等待人工确认。"
    )
    session.add(
        Event(
            type="agent.direct.handoff",
            content_item_id=bundle.task.content_item_id,
            project_id=project_id,
            payload={
                "task_id": bundle.task.id,
                "account_id": account_id,
                "actor_user_id": user.id,
            },
        )
    )
    await session.commit()
    return bundle.task, prompt


async def suggest_agent_run_knowledge(
    session: AsyncSession,
    *,
    user: User,
    task_id: int,
) -> KnowledgeSuggestion:
    bundle = await load_agent_run(session, task_id, user.org_id)
    project_id = bundle.task.brief.project_id
    account_id = bundle.task.brief.account_ids[0] if bundle.task.brief.account_ids else None
    if project_id is None or account_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="专家任务缺少工作范围")
    project, _account = await _require_scope(
        session,
        user=user,
        project_id=project_id,
        account_id=account_id,
        roles=_OPERATING_ROLES,
    )
    if project.client_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前项目尚未绑定客户")
    existing = await session.scalar(
        select(KnowledgeSuggestion).where(
            KnowledgeSuggestion.source_task_id == bundle.task.id,
            KnowledgeSuggestion.source_deliverable_id == bundle.deliverable.id,
        )
    )
    if existing is not None:
        return existing
    suggestion = KnowledgeSuggestion(
        org_id=user.org_id,
        client_id=project.client_id,
        project_id=project.id,
        category=_knowledge_category(bundle.invocation.agent_code),
        title=bundle.acceptance.title,
        content=_knowledge_content(bundle.deliverable.payload),
        payload={},
        tags=[bundle.invocation.agent_name],
        source_agent_code=bundle.invocation.agent_code.value,
        source_label=f"{bundle.invocation.agent_name} · 专家成果 #{bundle.task.id}",
        source_task_id=bundle.task.id,
        source_deliverable_id=bundle.deliverable.id,
        status="pending",
    )
    session.add(suggestion)
    await session.flush()
    session.add(
        Event(
            type="knowledge.suggested.from_agent",
            content_item_id=bundle.task.content_item_id,
            project_id=project.id,
            payload={
                "suggestion_id": suggestion.id,
                "task_id": bundle.task.id,
                "agent_code": bundle.invocation.agent_code.value,
                "actor_user_id": user.id,
            },
        )
    )
    await session.commit()
    await session.refresh(suggestion)
    return suggestion


async def load_agent_run(
    session: AsyncSession, task_id: int, org_id: int
) -> AgentRunBundle:
    task = await _load_direct_task(session, task_id, org_id)
    invocation = await session.scalar(
        select(AgentInvocation)
        .where(AgentInvocation.task_id == task.id)
        .order_by(AgentInvocation.id.desc())
    )
    deliverable = await session.scalar(
        select(Deliverable)
        .where(Deliverable.content_item_id == task.content_item_id)
        .order_by(Deliverable.id.desc())
    )
    acceptance = await session.scalar(
        select(DeliverableAcceptance)
        .where(DeliverableAcceptance.task_id == task.id)
        .order_by(DeliverableAcceptance.id.desc())
    )
    if invocation is None or deliverable is None or acceptance is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="专家任务尚未生成正式成果")
    entry_ids = list(
        await session.scalars(
            select(KnowledgeCitation.entry_id)
            .where(KnowledgeCitation.task_id == task.id)
            .order_by(KnowledgeCitation.id)
        )
    )
    knowledge_sources: list[KnowledgeEntry] = []
    if entry_ids:
        rows = list(
            await session.scalars(select(KnowledgeEntry).where(KnowledgeEntry.id.in_(entry_ids)))
        )
        by_id = {row.id: row for row in rows}
        knowledge_sources = [by_id[entry_id] for entry_id in entry_ids if entry_id in by_id]
    return AgentRunBundle(task, invocation, deliverable, acceptance, knowledge_sources)


async def _load_direct_task(session: AsyncSession, task_id: int, org_id: int) -> BrainTask:
    task = await session.scalar(
        select(BrainTask)
        .options(selectinload(BrainTask.brief), selectinload(BrainTask.plan))
        .where(
            BrainTask.id == task_id,
            BrainTask.org_id == org_id,
            BrainTask.runtime_mode == "direct_agent",
        )
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="专家任务不存在")
    return task


async def _require_scope(
    session: AsyncSession,
    *,
    user: User,
    project_id: int,
    account_id: int,
    roles: set[WorkspaceRole] | None,
):
    project = await require_project_access(session, user, project_id, roles=roles)
    account = await require_account_access(session, user, account_id, roles=roles)
    linked_id = await session.scalar(
        select(ProjectAccount.id).where(
            ProjectAccount.project_id == project.id,
            ProjectAccount.account_id == account.id,
        )
    )
    if account.project_id != project.id and linked_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="账号未绑定当前项目")
    if account.status != AccountStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前账号已停用")
    return project, account


def _assert_task_scope(task: BrainTask, project_id: int, account_id: int) -> None:
    if task.brief.project_id != project_id or account_id not in task.brief.account_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="来源成果不属于当前账号",
        )


def _spec(code: AgentCode) -> AgentSpec:
    spec = AGENT_SPECS.get(code)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="运营大脑请从主 Agent 对话入口使用",
        )
    return spec


def _plan_step(code: AgentCode, spec: AgentSpec, prompt: str, step_status: str) -> dict:
    return {
        "id": f"direct-{code.value}",
        "agent_code": code.value,
        "agent_name": spec.name,
        "phase": "独立专家调用",
        "intent": prompt,
        "status": step_status,
        "depends_on": [],
        "expected_output": spec.deliverable_title,
        "risk_level": "low",
    }


async def _model_name(session: AsyncSession, org_id: int, code: AgentCode) -> str:
    config = await session.scalar(
        select(ModelConfig).where(
            ModelConfig.org_id == org_id,
            ModelConfig.agent_code == code,
        )
    )
    return config.primary_model if config is not None else "deepseek-chat"


def _payload_summary(payload: dict) -> str:
    for key in ("account_persona", "summary", "title", "objective", "visual_style"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in payload.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "专家已生成结构化成果，请展开查看详情。"


def _acceptance_items(payload: dict) -> list[dict]:
    labels = {
        "account_persona": "账号定位",
        "target_audience": "目标人群",
        "differentiation": "差异化方向",
        "content_pillars": "内容支柱",
        "summary": "核心结论",
        "title": "成果标题",
        "objective": "目标",
        "risk_controls": "风险控制",
    }
    items = []
    for key, value in payload.items():
        if len(items) >= 6:
            break
        if value in (None, "", [], {}):
            continue
        items.append(
            {
                "label": labels.get(key, key.replace("_", " ")),
                "status": "pending",
                "note": "请确认该项是否符合当前账号实际情况。",
            }
        )
    return items


def _knowledge_category(code: AgentCode) -> KnowledgeCategory:
    if code == AgentCode.POSITIONING:
        return KnowledgeCategory.USER_PERSONA
    if code in {AgentCode.CONTENT_DIRECTOR, AgentCode.CUSTOMER_SERVICE}:
        return KnowledgeCategory.SCRIPT_LIBRARY
    if code in {AgentCode.ART_DIRECTOR, AgentCode.VIDEO_CREATOR, AgentCode.EDITOR}:
        return KnowledgeCategory.PROMPT_LIBRARY
    return KnowledgeCategory.HOT_CONTENT


def _knowledge_content(payload: dict) -> str:
    lines: list[str] = []
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        label = key.replace("_", " ")
        if isinstance(value, list):
            rendered = "、".join(str(item) for item in value)
        elif isinstance(value, dict):
            rendered = "；".join(
                f"{item_key}：{item_value}" for item_key, item_value in value.items()
            )
        else:
            rendered = str(value)
        lines.append(f"{label}：{rendered}")
    return "\n\n".join(lines) or "该专家成果已建议沉淀，请审核后补充正文。"
