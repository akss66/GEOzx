"""运营大脑与既有内容流水线之间的适配层。"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.approval_audit import add_approval_requested
from app.models import (
    AgentInvocation,
    AgentTask,
    AgentToolCall,
    BrainTask,
    ContentItem,
    Deliverable,
    DeliverableAcceptance,
    GateApproval,
    Project,
)
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    AgentTaskStatus,
    BrainTaskStatus,
    ContentStage,
    ContentStatus,
    DeliverableAcceptanceStatus,
    DeliverableStatus,
    DeliverableType,
    GateStatus,
    GateType,
    ProjectStatus,
    RerunScope,
)
from app.orchestrator.engine import OrchestrationEngine

_AGENT_CODE_MAP = {
    "01-positioning": AgentCode.POSITIONING,
    "02-content": AgentCode.CONTENT_DIRECTOR,
    "03-art": AgentCode.ART_DIRECTOR,
    "04-video": AgentCode.VIDEO_CREATOR,
    "05-editing": AgentCode.EDITOR,
    "06-operation": AgentCode.OPERATOR,
}

_AGENT_NAME = {
    AgentCode.POSITIONING: "账号定位专家",
    AgentCode.CONTENT_DIRECTOR: "编导文案专家",
    AgentCode.ART_DIRECTOR: "美术提示词专家",
    AgentCode.VIDEO_CREATOR: "视频创作专家",
    AgentCode.EDITOR: "剪辑专家",
    AgentCode.OPERATOR: "账号运营专家",
}

_INVOCATION_STATUS = {
    AgentTaskStatus.PENDING: AgentInvocationStatus.QUEUED,
    AgentTaskStatus.RUNNING: AgentInvocationStatus.RUNNING,
    AgentTaskStatus.DONE: AgentInvocationStatus.DONE,
    AgentTaskStatus.FAILED: AgentInvocationStatus.FAILED,
    AgentTaskStatus.BLOCKED: AgentInvocationStatus.BLOCKED,
}

_DELIVERABLE_TITLE = {
    DeliverableType.POSITIONING_STRATEGY: "定位策略",
    DeliverableType.TOPIC_PLAN: "选题计划",
    DeliverableType.PUBLISH_CALENDAR: "发布日历",
    DeliverableType.VIDEO_SCRIPT: "短视频脚本包",
    DeliverableType.ART_PROMPT: "美术提示词",
    DeliverableType.VIDEO_ASSET: "视频素材",
    DeliverableType.EDITED_VIDEO: "成片",
    DeliverableType.REVIEW_REPORT: "复盘报告",
    DeliverableType.AD_PLAN: "投放计划",
    DeliverableType.CS_RECORD: "客服记录",
}

_TOOL_NAME = {
    "account_context": "Account Context",
    "profile_snapshot": "Profile Snapshot",
    "brief_builder": "Brief Builder",
    "compliance_precheck": "Compliance Precheck",
    "style_prompt_builder": "Style Prompt Builder",
    "material_validator": "Material Validator",
    "publish_package_prepare": "Publish Package Prepare",
    "review_metrics": "Review Metrics",
    "agent_runtime": "Agent Runtime",
}

_RERUN_STAGE = {
    DeliverableType.POSITIONING_STRATEGY: ContentStage.POSITIONING,
    DeliverableType.VIDEO_SCRIPT: ContentStage.CONTENT_DIRECTION,
    DeliverableType.ART_PROMPT: ContentStage.ART_DIRECTION,
    DeliverableType.VIDEO_ASSET: ContentStage.VIDEO_CREATION,
    DeliverableType.EDITED_VIDEO: ContentStage.EDITING,
    DeliverableType.REVIEW_REPORT: ContentStage.OPERATION,
}

_RUNTIME_STAGE_BY_CODE = {
    AgentCode.POSITIONING.value: ContentStage.POSITIONING,
    AgentCode.CONTENT_DIRECTOR.value: ContentStage.CONTENT_DIRECTION,
    AgentCode.ART_DIRECTOR.value: ContentStage.ART_DIRECTION,
    AgentCode.VIDEO_CREATOR.value: ContentStage.VIDEO_CREATION,
    AgentCode.EDITOR.value: ContentStage.EDITING,
    AgentCode.OPERATOR.value: ContentStage.OPERATION,
}


async def _noop_emit(
    event_type: str,
    payload: dict | None = None,
    content_item_id: int | None = None,
    project_id: int | None = None,
) -> None:
    return None


_engine = OrchestrationEngine(emit=_noop_emit)


async def run_brain_task_pipeline(session: AsyncSession, task: BrainTask) -> BrainTask:
    """确认任务后启动既有流水线，并同步专家团调用与分项验收视图。"""

    content_item = await ensure_content_item(session, task)
    await _engine.start(session, content_item.id)
    await sync_brain_task_from_pipeline(session, task)
    await session.commit()
    return task


async def run_brain_task_steps(
    session: AsyncSession,
    task: BrainTask,
    agent_codes: list[str],
) -> BrainTask:
    """Run only the expert stages selected for the current smart-runtime round."""

    stages = {
        _RUNTIME_STAGE_BY_CODE[code]
        for code in agent_codes
        if code in _RUNTIME_STAGE_BY_CODE
    }
    if not stages:
        return task
    content_item = await ensure_content_item(session, task)
    await _engine.start(session, content_item.id, allowed_stages=stages)
    await sync_brain_task_from_pipeline(session, task)
    await session.commit()
    return task


async def rerun_brain_acceptance(
    session: AsyncSession,
    task: BrainTask,
    acceptance: DeliverableAcceptance,
    rerun_scope: RerunScope,
    reason: str,
) -> DeliverableAcceptance:
    """按验收项重跑对应旧流水线阶段，并生成新的验收版本。"""

    if task.content_item_id is None:
        acceptance.status = DeliverableAcceptanceStatus.RERUN_REQUESTED
        acceptance.reviewer_note = reason
        acceptance.rerun_scope = rerun_scope
        return acceptance

    stage = _RERUN_STAGE.get(acceptance.deliverable_type)
    if stage is None:
        acceptance.status = DeliverableAcceptanceStatus.RERUN_REQUESTED
        acceptance.reviewer_note = reason
        acceptance.rerun_scope = rerun_scope
        acceptance.brain_rejudge_summary = "该交付物暂不支持自动重跑，已进入人工处理队列。"
        return acceptance

    acceptance.status = DeliverableAcceptanceStatus.RERUN_REQUESTED
    acceptance.reviewer_note = reason
    acceptance.rerun_scope = rerun_scope
    acceptance.brain_rejudge_summary = "运营大脑已按当前交付物范围触发重跑，并保留历史版本。"
    await session.flush()

    await _engine.rerun_stage(session, task.content_item_id, stage)
    await sync_brain_task_from_pipeline(session, task)

    latest = await _latest_acceptance(session, task.id, acceptance.deliverable_type)
    return latest or acceptance


async def sync_brain_task_from_pipeline(session: AsyncSession, task: BrainTask) -> None:
    if task.content_item_id is None:
        return
    content_item = await session.get(ContentItem, task.content_item_id)
    if content_item is None:
        return
    await _sync_invocations(session, task)
    await _sync_acceptances(session, task)
    await _sync_task_status(session, task, content_item)
    await _sync_tool_calls(session, task)


async def ensure_content_item(session: AsyncSession, task: BrainTask) -> ContentItem:
    if task.content_item_id is not None:
        content_item = await session.get(ContentItem, task.content_item_id)
        if content_item is not None:
            return content_item

    project = await _resolve_project(session, task)
    account_id = task.brief.account_ids[0] if task.brief and task.brief.account_ids else None
    content_item = ContentItem(project_id=project.id, account_id=account_id, title=task.title)
    session.add(content_item)
    await session.flush()
    task.content_item_id = content_item.id
    return content_item


async def _resolve_project(session: AsyncSession, task: BrainTask) -> Project:
    if task.brief and task.brief.project_id is not None:
        project = await session.get(Project, task.brief.project_id)
        if project is not None and project.org_id == task.org_id:
            return project

    project = await session.scalar(
        select(Project)
        .where(Project.org_id == task.org_id, Project.status == ProjectStatus.ACTIVE)
        .order_by(Project.id)
    )
    if project is None:
        project = Project(org_id=task.org_id, name="运营大脑默认项目")
        session.add(project)
        await session.flush()

    if task.brief:
        task.brief.project_id = project.id
        task.brief.project_name = project.name
    return project


async def _sync_invocations(session: AsyncSession, task: BrainTask) -> None:
    old_tasks = (
        await session.scalars(
            select(AgentTask)
            .where(AgentTask.content_item_id == task.content_item_id)
            .order_by(AgentTask.id)
        )
    ).all()
    existing = {
        row.agent_code: row
        for row in (
            await session.scalars(
                select(AgentInvocation)
                .where(AgentInvocation.task_id == task.id)
                .order_by(AgentInvocation.id)
            )
        ).all()
    }
    upstream_ids: list[int] = []
    for old_task in old_tasks:
        code = _map_agent_code(old_task.agent_code)
        current = existing.get(code)
        deliverable = (
            await session.get(Deliverable, old_task.output_deliverable_id)
            if old_task.output_deliverable_id
            else None
        )
        if current is None:
            current = AgentInvocation(
                task_id=task.id,
                agent_code=code,
                agent_name=_AGENT_NAME[code],
            )
            session.add(current)
            await session.flush()
            existing[code] = current

        current.status = _INVOCATION_STATUS[old_task.status]
        current.input_summary = _input_summary(old_task.stage)
        current.output_summary = _payload_summary(deliverable.payload) if deliverable else ""
        current.model = "deepseek-chat"
        current.token_count = 0
        current.cost = Decimal("0")
        current.failure_reason = old_task.error
        current.upstream = upstream_ids.copy()
        current.started_at = old_task.started_at
        current.finished_at = old_task.finished_at
        upstream_ids.append(current.id)


async def _sync_acceptances(session: AsyncSession, task: BrainTask) -> None:
    deliverables = (
        await session.scalars(
            select(Deliverable)
            .where(
                Deliverable.content_item_id == task.content_item_id,
                Deliverable.status != DeliverableStatus.SUPERSEDED,
            )
            .order_by(Deliverable.id)
        )
    ).all()
    existing = {
        row.deliverable_id: row
        for row in (
            await session.scalars(
                select(DeliverableAcceptance).where(DeliverableAcceptance.task_id == task.id)
            )
        ).all()
        if row.deliverable_id is not None
    }
    for deliverable in deliverables:
        code = _map_agent_code(deliverable.agent_code)
        acceptance = existing.get(deliverable.id)
        is_new = acceptance is None
        if acceptance is None:
            acceptance = DeliverableAcceptance(
                task_id=task.id,
                deliverable_id=deliverable.id,
                agent_code=code,
                agent_name=_AGENT_NAME[code],
                deliverable_type=deliverable.type,
                title=_DELIVERABLE_TITLE.get(deliverable.type, "交付物"),
            )
            session.add(acceptance)
        acceptance.agent_code = code
        acceptance.agent_name = _AGENT_NAME[code]
        acceptance.deliverable_type = deliverable.type
        acceptance.title = _DELIVERABLE_TITLE.get(deliverable.type, "交付物")
        acceptance.version = deliverable.version
        acceptance.summary = _payload_summary(deliverable.payload)
        acceptance.acceptance_items = _acceptance_items(deliverable)
        acceptance.history_versions = await _history_versions(session, deliverable)
        acceptance.status = _acceptance_status(deliverable.status, acceptance.status)
        acceptance.brain_rejudge_basis = [
            "该验收项来自既有内容流水线的版本化交付物。",
            "如用户要求重跑，运营大脑会优先限定在当前交付物及受影响下游。",
        ]
        if is_new and acceptance.status == DeliverableAcceptanceStatus.PENDING:
            await session.flush()
            await add_approval_requested(
                session,
                org_id=task.org_id,
                project_id=await _task_project_id(session, task),
                content_item_id=task.content_item_id,
                approval_kind="deliverable",
                source_id=acceptance.id,
                title=acceptance.title,
            )


async def _sync_tool_calls(session: AsyncSession, task: BrainTask) -> None:
    invocations = (
        await session.scalars(
            select(AgentInvocation)
            .where(AgentInvocation.task_id == task.id)
            .order_by(AgentInvocation.id)
        )
    ).all()
    existing = {
        (row.invocation_id, row.tool_code): row
        for row in (
            await session.scalars(select(AgentToolCall).where(AgentToolCall.task_id == task.id))
        ).all()
    }
    step_by_agent = {
        step.get("agent_code"): step
        for step in (task.plan.steps if task.plan is not None else [])
        if isinstance(step, dict)
    }

    for invocation in invocations:
        agent_code = _agent_code_value(invocation.agent_code)
        step = step_by_agent.get(agent_code)
        tool_codes = step.get("tool_codes") if step else None
        if tool_codes is None:
            tool_codes = ["agent_runtime"]
        for tool_code in tool_codes:
            key = (invocation.id, tool_code)
            current = existing.get(key)
            previous_status = current.status if current is not None else None
            if current is None:
                current = AgentToolCall(
                    org_id=task.org_id,
                    task_id=task.id,
                    invocation_id=invocation.id,
                    module="brain",
                    agent_code=agent_code,
                    tool_code=tool_code,
                    tool_name=_TOOL_NAME.get(tool_code, tool_code.replace("_", " ").title()),
                )
                session.add(current)
                existing[key] = current

            configured_mode = (
                (step.get("tool_permissions") or {}).get(tool_code)
                if step is not None
                else None
            )
            permission_mode = configured_mode or (
                "confirm" if step and step.get("human_gate") else "auto"
            )
            requires_confirmation = permission_mode in {"confirm", "manual"}
            current.org_id = task.org_id
            current.task_id = task.id
            current.invocation_id = invocation.id
            current.agent_code = agent_code
            current.status = _tool_status(invocation.status, requires_confirmation, task.status)
            current.permission_mode = permission_mode
            current.requires_human_confirmation = requires_confirmation
            current.input_summary = invocation.input_summary
            current.output_summary = (
                invocation.output_summary
                or f"{current.tool_name} completed for {invocation.agent_name}"
            )
            current.error = invocation.failure_reason
            current.latency_ms = _latency_ms(invocation.started_at, invocation.finished_at)
            current.cost = Decimal("0")
            current.meta = {
                "execution_kind": step.get("execution_kind") if step else "agent_runtime",
                "agent_name": invocation.agent_name,
                "source": "pipeline_sync",
                "quality_gates": step.get("quality_gates", []) if step else [],
            }
            current.started_at = invocation.started_at
            current.finished_at = invocation.finished_at
            if current.status == "waiting_approval" and previous_status != "waiting_approval":
                await session.flush()
                await add_approval_requested(
                    session,
                    org_id=task.org_id,
                    project_id=await _task_project_id(session, task),
                    content_item_id=task.content_item_id,
                    approval_kind="tool_call",
                    source_id=current.id,
                    title=current.tool_name,
                )


async def _task_project_id(session: AsyncSession, task: BrainTask) -> int | None:
    if task.brief and task.brief.project_id is not None:
        return task.brief.project_id
    if task.content_item_id is None:
        return None
    content_item = await session.get(ContentItem, task.content_item_id)
    return content_item.project_id if content_item is not None else None


async def _sync_task_status(
    session: AsyncSession, task: BrainTask, content_item: ContentItem
) -> None:
    old_tasks = (
        await session.scalars(select(AgentTask).where(AgentTask.content_item_id == content_item.id))
    ).all()
    pending_gate = await session.scalar(
        select(GateApproval)
        .where(
            GateApproval.content_item_id == content_item.id,
            GateApproval.status == GateStatus.PENDING,
        )
        .order_by(GateApproval.id.desc())
    )
    done = sum(1 for row in old_tasks if row.status == AgentTaskStatus.DONE)
    task.progress = max(task.progress, min(92, 12 + done * 13))

    if pending_gate is not None:
        task.status = BrainTaskStatus.PENDING_ACCEPTANCE
        task.current_focus = f"等待质量门确认：{_gate_label(pending_gate.gate)}"
    elif content_item.status == ContentStatus.PUBLISHED:
        task.status = BrainTaskStatus.PENDING_ACCEPTANCE
        task.progress = max(task.progress, 92)
        task.current_focus = "流水线已完成，等待用户分项验收"
    elif content_item.status == ContentStatus.BLOCKED:
        task.status = BrainTaskStatus.PENDING_ACCEPTANCE
        task.current_focus = "流水线已阻塞，等待用户处理"
    else:
        task.status = BrainTaskStatus.RUNNING
        task.current_focus = "运营大脑正在调度专家团"

    _sync_plan_steps(task, {row.stage for row in old_tasks if row.status == AgentTaskStatus.DONE})


def _sync_plan_steps(task: BrainTask, done_stages: set[ContentStage]) -> None:
    if task.plan is None:
        return
    done_codes: set[str] = set()
    if ContentStage.POSITIONING in done_stages:
        done_codes.add(AgentCode.POSITIONING.value)
    if ContentStage.CONTENT_DIRECTION in done_stages:
        done_codes.add(AgentCode.CONTENT_DIRECTOR.value)
    if ContentStage.OPERATION in done_stages:
        done_codes.add(AgentCode.OPERATOR.value)
    steps = []
    for step in task.plan.steps:
        next_step = dict(step)
        next_step["status"] = "done" if next_step.get("agent_code") in done_codes else "planned"
        steps.append(next_step)
    task.plan.steps = steps


def _tool_status(
    invocation_status: AgentInvocationStatus,
    requires_confirmation: bool,
    task_status: BrainTaskStatus,
) -> str:
    if invocation_status == AgentInvocationStatus.FAILED:
        return "failed"
    if invocation_status == AgentInvocationStatus.BLOCKED:
        return "blocked"
    if invocation_status == AgentInvocationStatus.RUNNING:
        return "running"
    if invocation_status == AgentInvocationStatus.QUEUED:
        return "planned"
    if requires_confirmation and task_status == BrainTaskStatus.PENDING_ACCEPTANCE:
        return "waiting_approval"
    return "success"


def _agent_code_value(agent_code: AgentCode | str) -> str:
    return agent_code.value if isinstance(agent_code, AgentCode) else agent_code


def _latency_ms(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    return max(0, int((finished_at - started_at).total_seconds() * 1000))


def _map_agent_code(agent_code: str) -> AgentCode:
    return _AGENT_CODE_MAP.get(agent_code, AgentCode.DECISION)


def _payload_summary(payload: dict[str, Any]) -> str:
    labels = {
        "account_persona": "账号定位",
        "target_audience": "目标人群",
        "differentiation": "差异化方向",
        "content_pillars": "内容支柱",
        "title": "标题",
        "hook": "开场钩子",
        "scenes": "内容结构",
        "body": "正文",
        "topics": "话题",
        "summary": "核心结论",
        "optimization_suggestions": "优化建议",
        "issues": "主要问题",
        "highlights": "表现亮点",
    }
    lines: list[str] = []
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        label = labels.get(key, key.replace("_", " "))
        if isinstance(value, list):
            items = [str(item).strip() for item in value[:4] if str(item).strip()]
            if items:
                lines.append(f"{label}：\n" + "\n".join(f"- {item}" for item in items))
            continue
        if isinstance(value, dict):
            compact = "；".join(
                f"{item_key}：{item_value}" for item_key, item_value in value.items()
            )
            if compact:
                lines.append(f"{label}：{compact}")
            continue
        lines.append(f"{label}：{value}")
    text = "\n\n".join(lines)
    return text if len(text) <= 900 else f"{text[:900]}..."


def _input_summary(stage: ContentStage) -> str:
    labels = {
        ContentStage.POSITIONING: "任务目标、平台范围、账号定位与风险约束。",
        ContentStage.CONTENT_DIRECTION: "定位策略与内容目标。",
        ContentStage.ART_DIRECTION: "脚本包、风格偏好与平台规格。",
        ContentStage.VIDEO_CREATION: "美术提示词、镜头动作与素材要求。",
        ContentStage.EDITING: "视频素材、字幕、节奏与平台变体。",
        ContentStage.OPERATION: "成片、发布建议、账号指标与复盘口径。",
    }
    return labels.get(stage, "运营大脑调度上下文。")


def _acceptance_items(deliverable: Deliverable) -> list[dict[str, str]]:
    return [
        {"label": "结构完整", "status": "pass", "note": "交付物已按 schema 产出。"},
        {"label": "版本可追踪", "status": "pass", "note": f"当前版本 v{deliverable.version}。"},
        {"label": "等待人工验收", "status": "warn", "note": "发布或重跑前需要用户确认。"},
    ]


async def _history_versions(
    session: AsyncSession, deliverable: Deliverable
) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(Deliverable)
            .where(
                Deliverable.content_item_id == deliverable.content_item_id,
                Deliverable.type == deliverable.type,
            )
            .order_by(Deliverable.version)
        )
    ).all()
    return [
        {
            "version": row.version,
            "status": row.status.value,
            "note": row.note or "",
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


def _acceptance_status(
    deliverable_status: DeliverableStatus, current: DeliverableAcceptanceStatus
) -> DeliverableAcceptanceStatus:
    if current in {
        DeliverableAcceptanceStatus.APPROVED,
        DeliverableAcceptanceStatus.RERUN_REQUESTED,
    }:
        return current
    if deliverable_status == DeliverableStatus.APPROVED:
        return DeliverableAcceptanceStatus.APPROVED
    if deliverable_status == DeliverableStatus.REJECTED:
        return DeliverableAcceptanceStatus.REJECTED
    return DeliverableAcceptanceStatus.PENDING


async def _latest_acceptance(
    session: AsyncSession, task_id: int, deliverable_type: DeliverableType
) -> DeliverableAcceptance | None:
    return await session.scalar(
        select(DeliverableAcceptance)
        .where(
            DeliverableAcceptance.task_id == task_id,
            DeliverableAcceptance.deliverable_type == deliverable_type,
        )
        .order_by(DeliverableAcceptance.version.desc(), DeliverableAcceptance.id.desc())
    )


def _gate_label(gate: GateType) -> str:
    labels = {
        GateType.POSITIONING_REVIEW: "定位审核",
        GateType.TOPIC_REVIEW: "选题审核",
        GateType.SCRIPT_COMPLIANCE: "脚本合规",
        GateType.FINAL_VIDEO_REVIEW: "成片审核",
        GateType.PRE_PUBLISH_REVIEW: "发布前审核",
        GateType.LARGE_AD_SPEND: "大额投放",
    }
    return labels.get(gate, gate.value)


def _iso(value: datetime | None) -> str:
    return (value or datetime.now(UTC)).isoformat()
