"""Unified approval workbench for gates, tool permissions and deliverables."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.approval_access import (
    can_decide_project,
    require_task_visibility,
    task_project_ids,
)
from app.core.auth import CurrentUser
from app.core.workspace_access import (
    accessible_project_ids,
    require_account_access,
    require_content_scope,
    require_project_access,
)
from app.db import get_session
from app.models import (
    Account,
    AgentToolCall,
    BrainTask,
    ComplianceCheck,
    ContentItem,
    Deliverable,
    DeliverableAcceptance,
    GateApproval,
    Project,
    TaskBrief,
)
from app.models.enums import (
    ComplianceRisk,
    DeliverableAcceptanceStatus,
    DeliverableStatus,
    DeliverableType,
    GateStatus,
    GateType,
)
from app.schemas.approval import (
    ApprovalCountsOut,
    ApprovalQueueItemOut,
    ApprovalWorkspaceOut,
)

router = APIRouter(prefix="/approvals", tags=["approvals"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

GATE_DELIVERABLE_TYPES: dict[GateType, tuple[DeliverableType, ...]] = {
    GateType.POSITIONING_REVIEW: (DeliverableType.POSITIONING_STRATEGY,),
    GateType.TOPIC_REVIEW: (DeliverableType.TOPIC_PLAN, DeliverableType.VIDEO_SCRIPT),
    GateType.SCRIPT_COMPLIANCE: (DeliverableType.VIDEO_SCRIPT,),
    GateType.FINAL_VIDEO_REVIEW: (DeliverableType.EDITED_VIDEO,),
    GateType.PRE_PUBLISH_REVIEW: (DeliverableType.EDITED_VIDEO, DeliverableType.VIDEO_SCRIPT),
    GateType.LARGE_AD_SPEND: (DeliverableType.AD_PLAN,),
}

GATE_LABELS = {
    GateType.POSITIONING_REVIEW: "定位审核",
    GateType.TOPIC_REVIEW: "选题审核",
    GateType.SCRIPT_COMPLIANCE: "脚本合规",
    GateType.FINAL_VIDEO_REVIEW: "成片审核",
    GateType.PRE_PUBLISH_REVIEW: "发布前审核",
    GateType.LARGE_AD_SPEND: "投放审批",
}


async def _approval_project_scope(
    session: AsyncSession,
    user,
    client_id: int | None,
    project_id: int | None,
) -> set[int]:
    project_ids = await accessible_project_ids(session, user)
    if client_id is not None and project_ids:
        project_ids &= set(
            await session.scalars(
                select(Project.id).where(
                    Project.id.in_(project_ids),
                    Project.client_id == client_id,
                )
            )
        )
    if project_id is not None:
        project = await require_project_access(session, user, project_id)
        if client_id is not None and project.client_id != client_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="项目不属于当前客户",
            )
        project_ids &= {project_id}
    return project_ids


async def _latest_deliverable(
    session: AsyncSession,
    content_item_id: int,
    types: tuple[DeliverableType, ...],
) -> Deliverable | None:
    return await session.scalar(
        select(Deliverable)
        .where(
            Deliverable.content_item_id == content_item_id,
            Deliverable.type.in_(types),
            Deliverable.status != DeliverableStatus.SUPERSEDED,
        )
        .order_by(Deliverable.version.desc(), Deliverable.id.desc())
    )


def _deliverable_preview(deliverable: Deliverable | None) -> dict | None:
    if deliverable is None:
        return None
    return {
        "id": deliverable.id,
        "agent_code": deliverable.agent_code,
        "type": deliverable.type.value,
        "version": deliverable.version,
        "status": deliverable.status.value,
        "payload": deliverable.payload,
        "created_at": deliverable.created_at.isoformat(),
    }


def _gate_risk(gate: GateType, check: ComplianceCheck | None) -> str:
    if check is not None and check.risk == ComplianceRisk.BLOCK:
        return "critical"
    if gate in {GateType.SCRIPT_COMPLIANCE, GateType.PRE_PUBLISH_REVIEW, GateType.LARGE_AD_SPEND}:
        return "high"
    return "medium"


def _tool_risk(tool_call: AgentToolCall) -> str:
    if tool_call.tool_code in {
        "account_authorize",
        "account_delete",
        "credential_rotate",
    }:
        return "critical"
    if tool_call.tool_code == "publish_package_prepare" or tool_call.module in {
        "platform_integration",
        "external_action",
    }:
        return "high"
    return "medium"


def _tool_risk_reasons(tool_call: AgentToolCall, risk: str) -> list[str]:
    if tool_call.tool_code == "publish_package_prepare":
        return ["该动作会形成面向外部平台的正式发布清单", "发布前必须确认账号、素材与可见范围"]
    if risk == "critical":
        return ["该动作会改变账号、凭据或外部平台授权状态", "操作可能产生不可逆影响"]
    return ["Agent 请求执行受控工具", "需要确认输入、输出和影响范围"]


async def _task_context(
    session: AsyncSession,
    task: BrainTask,
    project_ids: set[int],
) -> tuple[Project, ContentItem | None, Account | None] | None:
    candidates = await task_project_ids(session, task)
    allowed = sorted(candidates & project_ids)
    if not allowed:
        return None
    project = await session.get(Project, allowed[0])
    if project is None:
        return None
    content_item = (
        await session.get(ContentItem, task.content_item_id)
        if task.content_item_id is not None
        else None
    )
    brief = await session.scalar(select(TaskBrief).where(TaskBrief.task_id == task.id))
    account_id = content_item.account_id if content_item is not None else None
    if account_id is None and brief is not None and brief.account_ids:
        account_id = next(
            (value for value in brief.account_ids if isinstance(value, int)),
            None,
        )
    account = await session.get(Account, account_id) if account_id is not None else None
    return project, content_item, account


@router.get("/workspace", response_model=ApprovalWorkspaceOut)
async def get_approval_workspace(
    user: CurrentUser,
    session: SessionDep,
    client_id: Annotated[int | None, Query()] = None,
    project_id: Annotated[int | None, Query()] = None,
    account_id: Annotated[int | None, Query()] = None,
) -> ApprovalWorkspaceOut:
    project_ids = await _approval_project_scope(
        session,
        user,
        client_id,
        project_id,
    )
    if account_id is not None:
        await require_account_access(session, user, account_id)
    if not project_ids:
        return ApprovalWorkspaceOut(
            items=[],
            counts=ApprovalCountsOut(),
            can_decide=False,
            generated_at=datetime.now(UTC),
        )

    items: list[ApprovalQueueItemOut] = []
    gate_rows = (
        await session.execute(
            select(GateApproval, ContentItem, Project, Account)
            .join(ContentItem, GateApproval.content_item_id == ContentItem.id)
            .join(Project, ContentItem.project_id == Project.id)
            .outerjoin(Account, ContentItem.account_id == Account.id)
            .where(
                GateApproval.status == GateStatus.PENDING,
                ContentItem.project_id.in_(project_ids),
            )
            .order_by(GateApproval.created_at, GateApproval.id)
        )
    ).all()
    for gate, content_item, project, account in gate_rows:
        if account_id is not None and content_item.account_id != account_id:
            continue
        try:
            await require_content_scope(
                session,
                user,
                project_id=content_item.project_id,
                account_id=content_item.account_id,
            )
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                continue
            raise
        check = await session.scalar(
            select(ComplianceCheck)
            .where(ComplianceCheck.content_item_id == content_item.id)
            .order_by(ComplianceCheck.id.desc())
        )
        deliverable = await _latest_deliverable(
            session,
            content_item.id,
            GATE_DELIVERABLE_TYPES[gate.gate],
        )
        risk = _gate_risk(gate.gate, check)
        reasons = ["该质量门会决定内容是否继续进入下游生产"]
        if check is not None:
            reasons.append(check.summary)
        items.append(
            ApprovalQueueItemOut(
                key=f"gate:{gate.id}",
                kind="gate",
                source_id=gate.id,
                project_id=project.id,
                project_name=project.name,
                account_id=content_item.account_id,
                account_name=account.nickname if account is not None else None,
                content_item_id=content_item.id,
                content_title=content_item.title,
                category=GATE_LABELS[gate.gate],
                title=content_item.title,
                summary=check.summary if check is not None else "等待人工检查正式成果。",
                risk_level=risk,
                risk_reasons=reasons,
                impact=["通过后 Agent 流程继续推进", "驳回后当前内容保持阻塞并记录修改意见"],
                agent_explanation="质量门由生产流程触发，系统不会自动越过人工审核。",
                preview={
                    "gate": gate.gate.value,
                    "deliverable": _deliverable_preview(deliverable),
                    "compliance": (
                        {
                            "risk": check.risk.value,
                            "summary": check.summary,
                            "findings": check.findings or [],
                        }
                        if check is not None
                        else None
                    ),
                },
                can_decide=await can_decide_project(session, user, project.id),
                created_at=gate.created_at,
            )
        )

    tool_calls = list(
        await session.scalars(
            select(AgentToolCall)
            .where(
                AgentToolCall.org_id == user.org_id,
                AgentToolCall.requires_human_confirmation.is_(True),
                AgentToolCall.status == "waiting_approval",
            )
            .order_by(AgentToolCall.created_at, AgentToolCall.id)
        )
    )
    for tool_call in tool_calls:
        task = await session.get(BrainTask, tool_call.task_id)
        if task is None:
            continue
        try:
            await require_task_visibility(session, user, task)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                continue
            raise
        context = await _task_context(session, task, project_ids)
        if context is None:
            continue
        project, content_item, account = context
        meta = tool_call.meta or {}
        package = meta.get("publish_package")
        package = package if isinstance(package, dict) else None
        item_account_id = (
            package.get("account_id")
            if package is not None and isinstance(package.get("account_id"), int)
            else account.id
            if account is not None
            else None
        )
        if account_id is not None and item_account_id != account_id:
            continue
        if account is None and item_account_id is not None:
            account = await session.get(Account, item_account_id)
        risk = _tool_risk(tool_call)
        items.append(
            ApprovalQueueItemOut(
                key=f"tool_call:{tool_call.id}",
                kind="tool_call",
                source_id=tool_call.id,
                project_id=project.id,
                project_name=project.name,
                account_id=item_account_id,
                account_name=account.nickname if account is not None else None,
                content_item_id=content_item.id if content_item is not None else None,
                content_title=content_item.title if content_item is not None else None,
                task_id=task.id,
                category="发布包确认" if package is not None else "Agent 工具权限",
                title=(
                    str(meta.get("content_title") or task.title)
                    if package is not None
                    else tool_call.tool_name
                ),
                summary=tool_call.output_summary or tool_call.input_summary,
                risk_level=risk,
                risk_reasons=_tool_risk_reasons(tool_call, risk),
                impact=(
                    ["确认后进入人工发布流程，不会自动发布", "驳回后发布包标记为不可执行"]
                    if package is not None
                    else ["确认后 Runtime 恢复执行", "驳回后工具调用失败并保留审计记录"]
                ),
                agent_explanation=tool_call.output_summary or "Agent 请求执行受控动作。",
                preview={
                    "tool_code": tool_call.tool_code,
                    "tool_name": tool_call.tool_name,
                    "input_summary": tool_call.input_summary,
                    "output_summary": tool_call.output_summary,
                    "publish_package": package,
                    "findings": meta.get("findings")
                    if isinstance(meta.get("findings"), list)
                    else [],
                    "matrix_plan_id": meta.get("matrix_plan_id"),
                    "matrix_item_id": meta.get("matrix_item_id"),
                },
                can_decide=await can_decide_project(session, user, project.id),
                created_at=tool_call.created_at,
            )
        )

    acceptances = list(
        await session.scalars(
            select(DeliverableAcceptance)
            .join(BrainTask, DeliverableAcceptance.task_id == BrainTask.id)
            .where(
                BrainTask.org_id == user.org_id,
                DeliverableAcceptance.status == DeliverableAcceptanceStatus.PENDING,
            )
            .order_by(DeliverableAcceptance.created_at, DeliverableAcceptance.id)
        )
    )
    for acceptance in acceptances:
        task = await session.get(BrainTask, acceptance.task_id)
        if task is None:
            continue
        try:
            await require_task_visibility(session, user, task)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                continue
            raise
        context = await _task_context(session, task, project_ids)
        if context is None:
            continue
        project, content_item, account = context
        if account_id is not None and (account is None or account.id != account_id):
            continue
        deliverable = (
            await session.get(Deliverable, acceptance.deliverable_id)
            if acceptance.deliverable_id is not None
            else None
        )
        items.append(
            ApprovalQueueItemOut(
                key=f"deliverable:{acceptance.id}",
                kind="deliverable",
                source_id=acceptance.id,
                project_id=project.id,
                project_name=project.name,
                account_id=account.id if account is not None else None,
                account_name=account.nickname if account is not None else None,
                content_item_id=content_item.id if content_item is not None else None,
                content_title=content_item.title if content_item is not None else None,
                task_id=task.id,
                category="正式成果验收",
                title=acceptance.title,
                summary=acceptance.summary,
                risk_level="medium",
                risk_reasons=["采用后该版本会成为后续工作的正式依据"],
                impact=["采用后任务可继续完成", "驳回并重跑会重新调用当前专家"],
                agent_explanation=(
                    f"{acceptance.agent_name} 已提交 v{acceptance.version} 成果，等待验收。"
                ),
                preview={
                    "acceptance": {
                        "id": acceptance.id,
                        "task_id": acceptance.task_id,
                        "deliverable_id": acceptance.deliverable_id,
                        "agent_code": acceptance.agent_code.value,
                        "agent_name": acceptance.agent_name,
                        "deliverable_type": acceptance.deliverable_type.value,
                        "title": acceptance.title,
                        "version": acceptance.version,
                        "summary": acceptance.summary,
                        "acceptance_items": acceptance.acceptance_items,
                        "history_versions": acceptance.history_versions,
                        "status": acceptance.status.value,
                        "reviewer_note": acceptance.reviewer_note,
                        "rerun_scope": (
                            acceptance.rerun_scope.value if acceptance.rerun_scope else None
                        ),
                        "brain_rejudge_summary": acceptance.brain_rejudge_summary,
                        "brain_rejudge_basis": acceptance.brain_rejudge_basis,
                    },
                    "deliverable": _deliverable_preview(deliverable),
                },
                can_decide=await can_decide_project(session, user, project.id),
                created_at=acceptance.created_at,
            )
        )

    kind_order = {"gate": 0, "tool_call": 1, "deliverable": 2}
    items.sort(key=lambda item: (kind_order[item.kind], item.created_at, item.source_id))
    counts = ApprovalCountsOut(
        total=len(items),
        critical=sum(item.risk_level == "critical" for item in items),
        high=sum(item.risk_level == "high" for item in items),
        medium=sum(item.risk_level == "medium" for item in items),
    )
    return ApprovalWorkspaceOut(
        items=items,
        counts=counts,
        can_decide=any(item.can_decide for item in items),
        generated_at=datetime.now(UTC),
    )
