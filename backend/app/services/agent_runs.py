"""Idempotency and lifecycle helpers for durable agent runs."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.runtime_failures import FailureDisposition
from app.models import (
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ConversationThread,
    Deliverable,
    SkillRun,
    ToolExecutionAttempt,
)
from app.services.run_revisions import cancel_revision_for_run
from app.services.runtime_locking import (
    RuntimeLockConflict,
    RuntimeRootLock,
    lock_runtime_root_scope,
    lock_runtime_run_headers,
)
from app.services.runtime_state import TERMINAL_STATUSES, RuntimeStateScope, close_runtime_state

ACTIVE_TASK_RUN_STATUSES = {
    "claimed",
    "queued",
    "running",
    "retry_wait",
}
WAITING_PREDECESSOR_STATUS = "waiting_predecessor"


def utc_now() -> datetime:
    return datetime.now(UTC)


async def claim_agent_run(
    session: AsyncSession,
    *,
    org_id: int,
    requested_by_id: int,
    client_message_id: str,
    request_payload: dict,
    thread_id: int | None = None,
    turn_id: int | None = None,
) -> tuple[AgentRun, bool]:
    """Atomically claim a client message or return its existing run."""

    run, claimed = await claim_agent_run_record(
        session,
        org_id=org_id,
        requested_by_id=requested_by_id,
        client_message_id=client_message_id,
        request_payload=request_payload,
        thread_id=thread_id,
        turn_id=turn_id,
    )
    if claimed:
        await session.commit()
        await session.refresh(run)
    return run, claimed


async def claim_agent_run_record(
    session: AsyncSession,
    *,
    org_id: int,
    requested_by_id: int,
    client_message_id: str,
    request_payload: dict,
    thread_id: int | None = None,
    turn_id: int | None = None,
) -> tuple[AgentRun, bool]:
    """Claim a run without committing the caller-owned transaction."""

    if turn_id is not None and thread_id is None:
        raise ValueError("turn_id requires thread_id")

    existing = await get_agent_run(
        session,
        org_id=org_id,
        requested_by_id=requested_by_id,
        client_message_id=client_message_id,
    )
    if existing is not None:
        _require_compatible_claim(
            existing,
            thread_id=thread_id,
            turn_id=turn_id,
            request_payload=request_payload,
        )
        return existing, False

    run = AgentRun(
        org_id=org_id,
        requested_by_id=requested_by_id,
        thread_id=thread_id,
        turn_id=turn_id,
        client_message_id=client_message_id,
        status="claimed",
        phase="request",
        max_attempts=settings.agent_run_max_attempts,
        request_payload=request_payload,
    )
    try:
        async with session.begin_nested():
            session.add(run)
            await session.flush()
    except IntegrityError:
        existing = await get_agent_run(
            session,
            org_id=org_id,
            requested_by_id=requested_by_id,
            client_message_id=client_message_id,
        )
        if existing is None:
            raise
        _require_compatible_claim(
            existing,
            thread_id=thread_id,
            turn_id=turn_id,
            request_payload=request_payload,
        )
        return existing, False
    return run, True


def _require_compatible_claim(
    run: AgentRun,
    *,
    thread_id: int | None,
    turn_id: int | None,
    request_payload: dict,
) -> None:
    """Prevent a V2 idempotency key from being rebound to another Turn."""

    is_turn_owned_claim = any(
        value is not None for value in (run.thread_id, run.turn_id, thread_id, turn_id)
    )
    if not is_turn_owned_claim:
        # Legacy BrainTask runs mutate request_payload as execution advances.
        # Their established idempotency behavior remains unchanged.
        return
    if (
        run.thread_id != thread_id
        or run.turn_id != turn_id
        or _immutable_request_payload(run.request_payload)
        != _immutable_request_payload(request_payload)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CLIENT_MESSAGE_CONFLICT",
                "message": "client_message_id is already bound to another request",
            },
        )


def _immutable_request_payload(value: dict) -> dict:
    """Compare only user-submitted fields; workers may add frozen execution context."""

    keys = (
        "account_id",
        "attachment_ids",
        "attachment_contexts",
        "client_message_id",
        "execution_preference",
        "message",
        "requested_skill_code",
        "start_new_turn",
        "target_turn_id",
        "trusted_structured_input",
        "thread_id",
        "turn_id",
    )
    return {key: value.get(key) for key in keys}


async def get_agent_run(
    session: AsyncSession,
    *,
    org_id: int,
    requested_by_id: int,
    client_message_id: str,
) -> AgentRun | None:
    return await session.scalar(
        select(AgentRun).where(
            AgentRun.org_id == org_id,
            AgentRun.requested_by_id == requested_by_id,
            AgentRun.client_message_id == client_message_id,
        )
    )


async def enqueue_agent_runtime(
    *,
    run_id: int,
) -> bool:
    """Enqueue a run once; return whether this call created the ARQ job."""

    from app.core.events import get_arq_pool

    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "execute_agent_run",
        run_id,
        _job_id=f"agent-run:{run_id}",
    )
    return job is not None


async def abort_agent_runtime(run_id: int) -> bool:
    from arq.jobs import Job

    from app.core.events import get_arq_pool

    pool = await get_arq_pool()
    try:
        return await Job(f"agent-run:{run_id}", pool).abort(
            timeout=0.25,
            poll_delay=0.05,
        )
    except TimeoutError:
        # The cancellation marker is already durable in Redis; the worker can
        # acknowledge it after this HTTP request returns.
        return True


async def mark_agent_run_queued(
    session: AsyncSession,
    run_id: int,
    *,
    task_id: int | None,
    request_payload: dict | None = None,
) -> AgentRun:
    run = await mark_agent_run_queued_record(
        session,
        run_id,
        task_id=task_id,
        request_payload=request_payload,
    )
    await session.commit()
    return run


async def mark_agent_run_queued_record(
    session: AsyncSession,
    run_id: int,
    *,
    task_id: int | None,
    request_payload: dict | None = None,
) -> AgentRun:
    """Mark one run queued without committing the caller-owned transaction."""

    with session.no_autoflush:
        discovered = await session.get(AgentRun, run_id)
    if discovered is None:
        raise ValueError(f"AgentRun not found: {run_id}")
    await lock_runtime_run_headers(
        session,
        run_ids=(run_id,),
        expected_task_ids=((task_id,) if task_id is not None else ()),
    )
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"AgentRun not found: {run_id}")
    run.task_id = task_id
    run.status = "queued"
    run.phase = "queued"
    run.lease_owner = None
    run.leased_until = None
    run.next_retry_at = None
    if request_payload is not None:
        run.request_payload = request_payload
    await session.flush()
    return run


async def queue_agent_run_behind_task(
    session: AsyncSession,
    run_id: int,
    *,
    task_id: int,
    request_payload: dict,
) -> bool:
    """Queue one follow-up without mutating the shared task before its turn."""

    waiting = await queue_agent_run_behind_task_record(
        session,
        run_id,
        task_id=task_id,
        request_payload=request_payload,
    )
    await session.commit()
    return waiting


async def queue_agent_run_behind_task_record(
    session: AsyncSession,
    run_id: int,
    *,
    task_id: int,
    request_payload: dict,
) -> bool:
    """Queue a follow-up in the caller-owned transaction."""

    with session.no_autoflush:
        contender_ids = tuple(
            await session.scalars(
                select(AgentRun.id)
                .where(
                    AgentRun.task_id == task_id,
                    AgentRun.id != run_id,
                    AgentRun.status.in_(
                        [*ACTIVE_TASK_RUN_STATUSES, WAITING_PREDECESSOR_STATUS]
                    ),
                )
                .order_by(AgentRun.id)
            )
        )
    await lock_runtime_run_headers(
        session,
        run_ids=(run_id, *contender_ids),
        expected_task_ids=(task_id,),
    )
    blocker = await session.scalar(
        select(AgentRun.id)
        .where(
            AgentRun.task_id == task_id,
            AgentRun.id != run_id,
            AgentRun.status.in_([*ACTIVE_TASK_RUN_STATUSES, WAITING_PREDECESSOR_STATUS]),
        )
        .order_by(AgentRun.id.desc())
        .limit(1)
    )
    if blocker is None:
        return False

    run = await session.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"AgentRun not found: {run_id}")
    run.task_id = task_id
    run.status = WAITING_PREDECESSOR_STATUS
    run.phase = WAITING_PREDECESSOR_STATUS
    run.request_payload = request_payload
    run.lease_owner = None
    run.leased_until = None
    run.next_retry_at = None
    await session.flush()
    return True


async def promote_next_waiting_agent_run(
    session: AsyncSession,
    task_id: int,
) -> AgentRun | None:
    """Promote the oldest follow-up only when no task run is executable."""

    with session.no_autoflush:
        run_ids = tuple(
            await session.scalars(
                select(AgentRun.id)
                .where(AgentRun.task_id == task_id)
                .order_by(AgentRun.id)
            )
        )
    await lock_runtime_run_headers(
        session,
        run_ids=run_ids,
        expected_task_ids=(task_id,),
    )
    active = await session.scalar(
        select(AgentRun.id)
        .where(
            AgentRun.task_id == task_id,
            AgentRun.status.in_(ACTIVE_TASK_RUN_STATUSES),
        )
        .order_by(AgentRun.id)
        .limit(1)
    )
    if active is not None:
        return None

    waiting = await session.scalar(
        select(AgentRun)
        .where(
            AgentRun.task_id == task_id,
            AgentRun.status == WAITING_PREDECESSOR_STATUS,
        )
        .order_by(AgentRun.id)
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if waiting is None:
        return None

    waiting.status = "queued"
    waiting.phase = "queued"
    waiting.lease_owner = None
    waiting.leased_until = None
    waiting.next_retry_at = None
    await session.commit()
    return waiting


async def acquire_agent_run(
    session: AsyncSession,
    run_id: int,
    *,
    worker_id: str,
    lease_seconds: int,
) -> AgentRun | None:
    run, runtime_lock = await _lock_agent_run_scope(
        session,
        run_id,
        include_cancellation_ledgers=True,
    )
    if run is None:
        return None

    now = utc_now()
    if run.cancel_requested_at is not None:
        active_root_id = await _active_root_skill_run_id(session, run.id)
        await cancel_revision_for_run(session, revision_run_id=run.id)
        await _cancel_active_skill_run_records(session, run.id)
        await close_runtime_state(
            session,
            scope=await _runtime_state_scope(
                session,
                run,
                preferred_skill_run_id=active_root_id,
                bind_latest_skill_run=False,
                include_content_item=True,
            ),
            status="cancelled",
            message="本轮执行已取消。",
            error_code="RUN_CANCELLED",
            prelocked=runtime_lock,
        )
        return None
    if run.status in {"completed", "cancelled", "dead_letter", "failed"}:
        return None
    if run.status == "retry_wait" and _is_future(run.next_retry_at, now):
        return None
    if run.status == "running" and run.lease_owner != worker_id:
        if _is_future(run.leased_until, now):
            return None

    run.attempt += 1
    run.lease_owner = worker_id
    run.leased_until = now + timedelta(seconds=max(1, lease_seconds))
    run.heartbeat_at = now
    run.started_at = run.started_at or now
    run.next_retry_at = None
    closure = await close_runtime_state(
        session,
        scope=await _runtime_state_scope(session, run, include_content_item=True),
        status="running",
        message="AgentRun acquired by worker.",
        prelocked=runtime_lock,
    )
    return closure.run


async def heartbeat_agent_run(
    session: AsyncSession,
    run_id: int,
    *,
    worker_id: str,
    lease_seconds: int,
) -> bool:
    run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None or run.status != "running" or run.lease_owner != worker_id:
        return False
    now = utc_now()
    run.heartbeat_at = now
    run.leased_until = now + timedelta(seconds=max(1, lease_seconds))
    await session.commit()
    return True


async def release_agent_run_failure(
    session: AsyncSession,
    run_id: int,
    *,
    disposition: FailureDisposition,
    error_code: str,
    error_detail: str,
    user_message: str | None = None,
    recovery_action: str | None = None,
) -> tuple[bool, int]:
    await session.rollback()
    run, runtime_lock = await _lock_agent_run_scope(
        session,
        run_id,
        include_cancellation_ledgers=False,
    )
    if run is None:
        return False, 0
    if run.status in {"failed", "dead_letter", "cancelled", "completed"}:
        return False, 0

    now = utc_now()
    run.heartbeat_at = now
    if disposition is FailureDisposition.TERMINAL:
        message = user_message or "任务未能继续执行，请检查配置后重试。"
        await close_runtime_state(
            session,
            scope=(
                await _runtime_state_scope(
                    session,
                    run,
                    result_payload={
                        "status": "failed",
                        "response": message,
                        "error_code": error_code[:120],
                        "recovery_action": recovery_action
                        or "请检查任务配置、权限和可用资源后重新提交。",
                    },
                    error_detail=error_detail[:4000],
                    include_content_item=True,
                )
            ),
            status="failed",
            message=message,
            error_code=error_code[:120],
            prelocked=runtime_lock,
        )
        return False, 0
    if run.attempt >= run.max_attempts:
        message = user_message or "任务多次重试仍未成功，本轮已停止。"
        await close_runtime_state(
            session,
            scope=(
                await _runtime_state_scope(
                    session,
                    run,
                    result_payload={
                        "status": "dead_letter",
                        "response": message,
                        "error_code": error_code[:120],
                    },
                    error_detail=error_detail[:4000],
                    include_content_item=True,
                )
            ),
            status="dead_letter",
            message=message,
            error_code=error_code[:120],
            prelocked=runtime_lock,
        )
        return False, 0

    retry_delay = min(5 * (2 ** max(0, run.attempt - 1)), 300)
    run.next_retry_at = now + timedelta(seconds=retry_delay)
    await close_runtime_state(
        session,
        scope=await _runtime_state_scope(
            session,
            run,
            error_detail=error_detail[:4000],
            include_content_item=True,
        ),
        status="retry_wait",
        message=user_message or "任务暂时失败，系统稍后自动重试。",
        error_code=error_code[:120],
        prelocked=runtime_lock,
    )
    return True, retry_delay


async def request_agent_run_cancel(session: AsyncSession, run_id: int) -> AgentRun | None:
    run = await request_agent_run_cancel_record(session, run_id)
    await session.commit()
    return run


async def request_agent_run_cancel_record(
    session: AsyncSession,
    run_id: int,
) -> AgentRun | None:
    """Idempotently request cancellation without committing the caller transaction."""

    run, _runtime_lock = await _lock_agent_run_scope(
        session,
        run_id,
        include_cancellation_ledgers=False,
    )
    if run is None:
        return None
    if run.status in {
        "completed",
        "blocked",
        "cancelled",
        "dead_letter",
        "failed",
        "stopped",
    }:
        return run
    if run.cancel_requested_at is None:
        run.cancel_requested_at = utc_now()
        run.phase = "cancel_requested"
        await session.flush()
    return run


async def cancel_agent_run(session: AsyncSession, run_id: int) -> None:
    await session.rollback()
    run, runtime_lock = await _lock_agent_run_scope(
        session,
        run_id,
        include_cancellation_ledgers=True,
    )
    if run is None:
        return
    active_root_id = await _active_root_skill_run_id(session, run.id)
    await cancel_revision_for_run(session, revision_run_id=run.id)
    await _cancel_active_skill_run_records(session, run.id)
    await close_runtime_state(
        session,
        scope=await _runtime_state_scope(
            session,
            run,
            preferred_skill_run_id=active_root_id,
            bind_latest_skill_run=False,
            include_content_item=True,
        ),
        status="cancelled",
        message="本轮执行已取消。",
        error_code="RUN_CANCELLED",
        prelocked=runtime_lock,
    )


async def _cancel_active_skill_run_records(session: AsyncSession, run_id: int) -> None:
    """Join every active child/parent SkillRun to the run cancellation transaction."""

    skill_runs = list(
        await session.scalars(
            select(SkillRun)
            .where(SkillRun.run_id == run_id)
            .execution_options(populate_existing=True)
        )
    )
    for skill_run in skill_runs:
        if skill_run.status in {"completed", "blocked", "failed", "cancelled", "stopped"}:
            continue
        skill_run.status = "cancelled"
        skill_run.error_code = "RUN_CANCELLED"
        skill_run.output_snapshot = {
            **dict(skill_run.output_snapshot or {}),
            "status": "cancelled",
            "error_code": "RUN_CANCELLED",
        }
    await session.flush()


async def _lock_agent_run_scope(
    session: AsyncSession,
    run_id: int,
    *,
    include_cancellation_ledgers: bool,
) -> tuple[AgentRun | None, RuntimeRootLock | None]:
    """Acquire one complete Run-root scope before any lifecycle mutation."""

    with session.no_autoflush:
        discovered = await session.get(AgentRun, run_id)
        discovered_task = (
            await session.get(BrainTask, discovered.task_id)
            if discovered is not None and discovered.task_id is not None
            else None
        )
    if discovered is None:
        return None, None
    with session.no_autoflush:
        discovered_skills = list(
            await session.scalars(
                select(SkillRun)
                .where(SkillRun.run_id == run_id)
                .order_by(SkillRun.id)
            )
        )
    active_skills = [
        item
        for item in discovered_skills
        if item.status not in {"completed", "blocked", "failed", "cancelled", "stopped"}
    ]
    fallback_root = active_skills[-1] if active_skills else None
    if fallback_root is None and discovered_skills:
        fallback_root = discovered_skills[-1]
    root = next(
        (item for item in active_skills if item.skill_code == "operation_iteration"),
        fallback_root,
    )
    root_id = root.id if root is not None else None
    other_skill_ids = tuple(
        item.id for item in discovered_skills if item.id != root_id
    )
    runtime_lock = await lock_runtime_root_scope(
        session,
        run_id=run_id,
        expected_turn_id=discovered.turn_id,
        expected_task_id=discovered.task_id,
        expected_content_item_id=(
            discovered_task.content_item_id if discovered_task is not None else None
        ),
        root_skill_run_id=root_id,
        child_skill_run_ids=other_skill_ids,
        validate_child_parent=False,
        include_run_revisions=include_cancellation_ledgers,
    )
    run = await session.get(AgentRun, run_id)
    return run, runtime_lock


async def _active_root_skill_run_id(session: AsyncSession, run_id: int) -> int | None:
    active = list(
        await session.scalars(
            select(SkillRun)
            .where(
                SkillRun.run_id == run_id,
                SkillRun.status.not_in(
                    {"completed", "blocked", "failed", "cancelled", "stopped"}
                ),
            )
            .order_by(SkillRun.id)
        )
    )
    if not active:
        return None
    root = next((item for item in active if item.skill_code == "operation_iteration"), None)
    return (root or active[0]).id


def _is_future(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return False
    comparable_now = now if value.tzinfo is not None else now.replace(tzinfo=None)
    return value > comparable_now


async def complete_agent_run(
    session: AsyncSession,
    run_id: int,
    *,
    task_id: int,
    status: str,
) -> None:
    with session.no_autoflush:
        discovered = await session.get(AgentRun, run_id)
        target_task = await session.get(BrainTask, task_id)
        if discovered is None:
            return
        if target_task is None:
            raise ValueError(f"BrainTask not found: {task_id}")
        skills = list(
            await session.scalars(
                select(SkillRun)
                .where(SkillRun.run_id == run_id)
                .order_by(SkillRun.id)
            )
        )
        roots = [
            item
            for item in skills
            if type(
                dict(item.output_snapshot or {}).get("composite_parent_skill_run_id")
            )
            is not int
        ]
        root = next(
            (item for item in roots if item.skill_code == "operation_iteration"),
            roots[0] if roots else None,
        )
        root_id = root.id if root is not None else None
        child_ids = tuple(item.id for item in skills if item.id != root_id)
        deliverable_ids = tuple(
            await session.scalars(
                select(Deliverable.id)
                .where(Deliverable.run_id == run_id)
                .order_by(Deliverable.id)
            )
        )
        invocation_ids = tuple(
            await session.scalars(
                select(AgentInvocation.id)
                .where(AgentInvocation.run_id == run_id)
                .order_by(AgentInvocation.id)
            )
        )
        skill_ids = tuple(item.id for item in skills)
        tool_call_ids = tuple(
            await session.scalars(
                select(AgentToolCall.id)
                .where(
                    AgentToolCall.skill_run_id.in_(skill_ids)
                    | AgentToolCall.invocation_id.in_(invocation_ids)
                )
                .order_by(AgentToolCall.id)
            )
        )
        attempt_ids = tuple(
            await session.scalars(
                select(ToolExecutionAttempt.id)
                .where(ToolExecutionAttempt.tool_call_id.in_(tool_call_ids))
                .order_by(ToolExecutionAttempt.id)
            )
        )
        runtime_lock = await lock_runtime_root_scope(
            session,
            run_id=run_id,
            expected_turn_id=discovered.turn_id,
            expected_task_id=discovered.task_id,
            expected_content_item_id=target_task.content_item_id,
            transition_task_id=task_id,
            root_skill_run_id=root_id,
            child_skill_run_ids=child_ids,
            validate_child_parent=False,
            include_run_revisions=True,
            deliverable_ids=deliverable_ids,
            invocation_ids=invocation_ids,
            tool_call_ids=tool_call_ids,
            attempt_ids=attempt_ids,
        )
    run = await session.get(AgentRun, run_id)
    task = await session.get(BrainTask, task_id)
    if run is None or task is None:
        raise RuntimeLockConflict("runtime completion scope changed after root lock")
    if run.status in TERMINAL_STATUSES and run.task_id != task_id:
        raise RuntimeLockConflict("terminal runtime cannot rebind its BrainTask")
    run.task_id = task_id
    await session.flush()
    await close_runtime_state(
        session,
        scope=await _runtime_state_scope(
            session,
            run,
            result_payload={"task_id": task_id, "task_status": status},
        ),
        status=status,
        message=task.current_focus or f"任务状态：{status}",
        prelocked=runtime_lock,
    )


async def fail_agent_run(session: AsyncSession, run_id: int, exc: Exception) -> None:
    await session.rollback()
    run, runtime_lock = await _lock_agent_run_scope(
        session,
        run_id,
        include_cancellation_ledgers=False,
    )
    if run is None:
        return
    await close_runtime_state(
        session,
        scope=await _runtime_state_scope(
            session,
            run,
            error_detail=str(exc)[:4000],
            include_content_item=True,
        ),
        status="failed",
        message="任务未能继续执行，请检查配置后重试。",
        error_code=type(exc).__name__,
        prelocked=runtime_lock,
    )


async def _runtime_state_scope(
    session: AsyncSession,
    run: AgentRun,
    *,
    result_payload: dict | None = None,
    error_detail: str | None = None,
    preferred_skill_run_id: int | None = None,
    bind_latest_skill_run: bool = True,
    include_content_item: bool = False,
) -> RuntimeStateScope:
    skill_run_id = preferred_skill_run_id
    if skill_run_id is None and bind_latest_skill_run:
        skill_run_id = await session.scalar(
            select(SkillRun.id)
            .where(
                SkillRun.run_id == run.id,
                SkillRun.status.not_in(
                    {"completed", "blocked", "failed", "cancelled", "stopped"}
                ),
            )
            .order_by(SkillRun.id.desc())
            .limit(1)
        )
    thread = (
        await session.get(ConversationThread, run.thread_id)
        if run.thread_id is not None
        else None
    )
    if run.thread_id is not None and thread is None:
        raise ValueError("AgentRun ConversationThread ownership is missing")
    if thread is not None and thread.org_id != run.org_id:
        raise ValueError("AgentRun ConversationThread ownership does not match")
    task = await session.get(BrainTask, run.task_id) if run.task_id is not None else None
    return RuntimeStateScope(
        run_id=run.id,
        org_id=run.org_id,
        thread_id=run.thread_id,
        turn_id=run.turn_id,
        skill_run_id=skill_run_id,
        task_id=run.task_id,
        account_id=thread.account_id if thread is not None else None,
        project_id=thread.project_id if thread is not None else None,
        content_item_id=(
            task.content_item_id if include_content_item and task is not None else None
        ),
        result_payload=result_payload,
        error_detail=error_detail,
    )
