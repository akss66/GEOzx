"""Account and user scoped projection for concrete operator work."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import require_account_access
from app.models import (
    AgentRun,
    ContentScheduleEntry,
    ConversationThread,
    Deliverable,
    Event,
    ShootTask,
    TurnInterrupt,
    User,
)
from app.schemas.pending_work import (
    AccountDataTarget,
    ConversationTurnTarget,
    PendingWorkCompletion,
    PendingWorkGroup,
    PendingWorkItem,
    PendingWorkResponse,
    TaskWorkspaceTarget,
)
from app.services.data_import.service import account_status_summary

_GROUPS = (
    ("clarification", "待补充资料"),
    ("approval", "待确认方向"),
    ("shoot_task", "待拍摄"),
    ("manual_publish", "待手动发布"),
    ("account_data", "待补录数据"),
)
_DATA_DOMAIN_LABELS = {
    "account_metrics": "账号概览",
    "content_metrics": "作品表现",
    "audience_profiles": "粉丝画像",
    "benchmarks": "对标数据",
}
_DATA_STATUS_LABELS = {
    "not_imported": "尚未导入",
    "stale": "需要更新",
    "failed": "导入失败",
}


@dataclass(frozen=True)
class PendingWorkLifecycleResult:
    response: PendingWorkCompletion
    event: Event


async def list_pending_work(
    session: AsyncSession,
    *,
    user: User,
    account_id: int,
) -> PendingWorkResponse:
    """Return only work the current user may perform for one authorized account."""

    await require_account_access(session, user, account_id)
    grouped: dict[str, list[PendingWorkItem]] = {kind: [] for kind, _ in _GROUPS}

    interrupts = list(
        await session.scalars(
            select(TurnInterrupt)
            .join(AgentRun, AgentRun.id == TurnInterrupt.run_id)
            .where(
                TurnInterrupt.org_id == user.org_id,
                TurnInterrupt.account_id == account_id,
                TurnInterrupt.status == "pending",
                AgentRun.org_id == user.org_id,
                AgentRun.requested_by_id == user.id,
            )
            .order_by(TurnInterrupt.created_at.asc(), TurnInterrupt.id.asc())
        )
    )
    for interrupt in interrupts:
        group_kind = "clarification" if interrupt.kind == "clarification" else "approval"
        grouped[group_kind].append(
            PendingWorkItem(
                id=f"interrupt:{interrupt.id}",
                kind=group_kind,
                action_label=(
                    "补充资料"
                    if group_kind == "clarification"
                    else interrupt.action_label or "确认后继续"
                ),
                account_id=account_id,
                thread_id=interrupt.thread_id,
                turn_id=interrupt.turn_id,
                reason=interrupt.public_message,
                next_step_after_completion="运营大脑会从当前步骤继续执行。",
                target=ConversationTurnTarget(
                    thread_id=interrupt.thread_id,
                    turn_id=interrupt.turn_id,
                ),
            )
        )

    shoot_rows = list(
        (
            await session.execute(
                select(ShootTask, ConversationThread.id, Deliverable.turn_id)
                .outerjoin(Deliverable, Deliverable.id == ShootTask.source_artifact_id)
                .outerjoin(
                    ConversationThread,
                    and_(
                        ConversationThread.id == Deliverable.thread_id,
                        ConversationThread.org_id == user.org_id,
                        ConversationThread.account_id == account_id,
                    ),
                )
                .where(
                    ShootTask.org_id == user.org_id,
                    ShootTask.account_id == account_id,
                    ShootTask.status == "pending",
                    or_(
                        ShootTask.assignee_id == user.id,
                        and_(
                            ShootTask.assignee_id.is_(None),
                            ShootTask.created_by_id == user.id,
                        ),
                    ),
                )
                .order_by(
                    ShootTask.due_at.is_(None),
                    ShootTask.due_at.asc(),
                    ShootTask.created_at.asc(),
                    ShootTask.id.asc(),
                )
            )
        ).all()
    )
    for shoot, thread_id, turn_id in shoot_rows:
        has_source = thread_id is not None and turn_id is not None
        grouped["shoot_task"].append(
            PendingWorkItem(
                id=f"shoot_task:{shoot.id}",
                kind="shoot_task",
                action_label="查看拍摄要求",
                account_id=account_id,
                thread_id=thread_id if has_source else None,
                turn_id=turn_id if has_source else None,
                due_at=shoot.due_at,
                reason=shoot.title,
                next_step_after_completion="完成后，这项拍摄工作会从待处理中移除。",
                target=(
                    ConversationTurnTarget(thread_id=thread_id, turn_id=turn_id)
                    if has_source
                    else TaskWorkspaceTarget()
                ),
            )
        )

    schedule_rows = list(
        (
            await session.execute(
                select(ContentScheduleEntry, ConversationThread.id, Deliverable.turn_id)
                .outerjoin(
                    Deliverable,
                    Deliverable.id == ContentScheduleEntry.source_artifact_id,
                )
                .outerjoin(
                    ConversationThread,
                    and_(
                        ConversationThread.id == Deliverable.thread_id,
                        ConversationThread.org_id == user.org_id,
                        ConversationThread.account_id == account_id,
                    ),
                )
                .where(
                    ContentScheduleEntry.org_id == user.org_id,
                    ContentScheduleEntry.account_id == account_id,
                    ContentScheduleEntry.created_by_id == user.id,
                    ContentScheduleEntry.status == "planned",
                )
                .order_by(
                    ContentScheduleEntry.scheduled_at.asc(),
                    ContentScheduleEntry.created_at.asc(),
                    ContentScheduleEntry.id.asc(),
                )
            )
        ).all()
    )
    for schedule, thread_id, turn_id in schedule_rows:
        has_source = thread_id is not None and turn_id is not None
        grouped["manual_publish"].append(
            PendingWorkItem(
                id=f"schedule_entry:{schedule.id}",
                kind="manual_publish",
                action_label="去完成发布",
                account_id=account_id,
                thread_id=thread_id if has_source else None,
                turn_id=turn_id if has_source else None,
                due_at=schedule.scheduled_at,
                reason="排期内容等待在抖音手动发布。",
                next_step_after_completion="记录发布完成后，可继续观察作品数据。",
                target=(
                    ConversationTurnTarget(thread_id=thread_id, turn_id=turn_id)
                    if has_source
                    else TaskWorkspaceTarget()
                ),
            )
        )

    summary = await account_status_summary(
        session,
        org_id=user.org_id,
        account_id=account_id,
    )
    inventory = summary.get("dataset_inventory", [])
    incomplete = [
        row
        for row in inventory
        if isinstance(row, dict)
        and row.get("status") in {"not_imported", "stale", "failed"}
    ]
    if incomplete:
        descriptions = [
            f"{_DATA_DOMAIN_LABELS.get(str(row.get('data_domain')), '账号数据')}"
            f"{_DATA_STATUS_LABELS.get(str(row.get('status')), '需要处理')}"
            for row in incomplete
        ]
        grouped["account_data"].append(
            PendingWorkItem(
                id=f"account_data:{account_id}",
                kind="account_data",
                action_label="补录账号数据",
                account_id=account_id,
                reason="；".join(descriptions),
                next_step_after_completion="数据确认后，运营大脑会在后续分析中使用最新数据。",
                target=AccountDataTarget(),
            )
        )

    return PendingWorkResponse(
        account_id=account_id,
        groups=[
            PendingWorkGroup(
                kind=kind,
                label=label,
                count=len(grouped[kind]),
                items=grouped[kind],
            )
            for kind, label in _GROUPS
        ],
    )


async def complete_shoot_task(
    session: AsyncSession,
    *,
    user: User,
    account_id: int,
    shoot_task_id: int,
) -> PendingWorkLifecycleResult:
    await require_account_access(session, user, account_id)
    row = await session.scalar(
        select(ShootTask)
        .where(
            ShootTask.id == shoot_task_id,
            ShootTask.org_id == user.org_id,
            ShootTask.account_id == account_id,
            or_(
                ShootTask.assignee_id == user.id,
                and_(ShootTask.assignee_id.is_(None), ShootTask.created_by_id == user.id),
            ),
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="待处理事项不存在")
    if row.status not in {"pending", "completed"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前拍摄任务无法完成")
    row.status = "completed"
    event = await _lifecycle_event(
        session,
        user=user,
        account_id=account_id,
        resource_kind="shoot_task",
        resource_id=row.id,
    )
    return PendingWorkLifecycleResult(
        response=PendingWorkCompletion(
            id=f"shoot_task:{row.id}",
            kind="shoot_task",
            account_id=account_id,
            event_id=event.id,
            next_step_after_completion="拍摄任务已完成，可继续进入剪辑或发布准备。",
        ),
        event=event,
    )


async def publish_schedule_entry(
    session: AsyncSession,
    *,
    user: User,
    account_id: int,
    schedule_entry_id: int,
) -> PendingWorkLifecycleResult:
    await require_account_access(session, user, account_id)
    row = await session.scalar(
        select(ContentScheduleEntry)
        .where(
            ContentScheduleEntry.id == schedule_entry_id,
            ContentScheduleEntry.org_id == user.org_id,
            ContentScheduleEntry.account_id == account_id,
            ContentScheduleEntry.created_by_id == user.id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="待处理事项不存在")
    if row.status not in {"planned", "published"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前排期无法记录发布")
    row.status = "published"
    event = await _lifecycle_event(
        session,
        user=user,
        account_id=account_id,
        resource_kind="schedule_entry",
        resource_id=row.id,
    )
    return PendingWorkLifecycleResult(
        response=PendingWorkCompletion(
            id=f"schedule_entry:{row.id}",
            kind="manual_publish",
            account_id=account_id,
            event_id=event.id,
            next_step_after_completion="发布记录已保存，可继续监测这条作品的数据。",
        ),
        event=event,
    )


async def _lifecycle_event(
    session: AsyncSession,
    *,
    user: User,
    account_id: int,
    resource_kind: str,
    resource_id: int,
) -> Event:
    raw_key = f"pending-work-v1:{user.org_id}:{account_id}:{resource_kind}:{resource_id}"
    key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    existing = await session.scalar(select(Event).where(Event.idempotency_key == key))
    if existing is not None:
        return existing
    event = Event(
        type="pending_work.updated",
        org_id=user.org_id,
        account_id=account_id,
        payload={
            "account_id": account_id,
            "resource_kind": resource_kind,
            "resource_id": resource_id,
            "change": "completed",
        },
        idempotency_key=key,
    )
    session.add(event)
    await session.flush()
    return event
