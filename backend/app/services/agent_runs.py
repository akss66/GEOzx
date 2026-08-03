"""Idempotency and lifecycle helpers for durable agent runs."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.runtime_failures import FailureDisposition
from app.models import AgentRun, BrainTask, SkillRun
from app.services.runtime_state import RuntimeStateScope, close_runtime_state

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
    session.add(run)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
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
    await session.refresh(run)
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
) -> None:
    from app.core.events import get_arq_pool

    pool = await get_arq_pool()
    job = await pool.enqueue_job(
        "execute_agent_run",
        run_id,
        _job_id=f"agent-run:{run_id}",
    )
    if job is None:
        raise RuntimeError(f"AgentRun queue claim already exists: {run_id}")


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
    await session.commit()
    return run


async def queue_agent_run_behind_task(
    session: AsyncSession,
    run_id: int,
    *,
    task_id: int,
    request_payload: dict,
) -> bool:
    """Queue one follow-up without mutating the shared task before its turn."""

    await session.scalar(select(BrainTask.id).where(BrainTask.id == task_id).with_for_update())
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
    await session.commit()
    return True


async def promote_next_waiting_agent_run(
    session: AsyncSession,
    task_id: int,
) -> AgentRun | None:
    """Promote the oldest follow-up only when no task run is executable."""

    await session.scalar(select(BrainTask.id).where(BrainTask.id == task_id).with_for_update())
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
        .with_for_update()
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
    run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        return None

    now = utc_now()
    if run.cancel_requested_at is not None:
        await close_runtime_state(
            session,
            scope=await _runtime_state_scope(session, run),
            status="cancelled",
            message="本轮执行已取消。",
            error_code="RUN_CANCELLED",
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
        scope=await _runtime_state_scope(session, run),
        status="running",
        message="AgentRun acquired by worker.",
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
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
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
                )
            ),
            status="failed",
            message=message,
            error_code=error_code[:120],
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
                )
            ),
            status="dead_letter",
            message=message,
            error_code=error_code[:120],
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
        ),
        status="retry_wait",
        message=user_message or "任务暂时失败，系统稍后自动重试。",
        error_code=error_code[:120],
    )
    return True, retry_delay


async def request_agent_run_cancel(session: AsyncSession, run_id: int) -> AgentRun | None:
    run = await session.get(AgentRun, run_id)
    if run is None:
        return None
    if run.status in {"completed", "cancelled", "dead_letter", "failed"}:
        return run
    run.cancel_requested_at = utc_now()
    run.phase = "cancel_requested"
    await session.commit()
    return run


async def cancel_agent_run(session: AsyncSession, run_id: int) -> None:
    await session.rollback()
    run = await session.get(AgentRun, run_id)
    if run is None:
        return
    await close_runtime_state(
        session,
        scope=await _runtime_state_scope(session, run),
        status="cancelled",
        message="本轮执行已取消。",
        error_code="RUN_CANCELLED",
    )


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
    run = await session.get(AgentRun, run_id)
    if run is None:
        return
    task = await session.get(BrainTask, task_id)
    if task is None:
        raise ValueError(f"BrainTask not found: {task_id}")
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
    )


async def fail_agent_run(session: AsyncSession, run_id: int, exc: Exception) -> None:
    await session.rollback()
    run = await session.get(AgentRun, run_id)
    if run is None:
        return
    await close_runtime_state(
        session,
        scope=await _runtime_state_scope(
            session,
            run,
            error_detail=str(exc)[:4000],
        ),
        status="failed",
        message="任务未能继续执行，请检查配置后重试。",
        error_code=type(exc).__name__,
    )


async def _runtime_state_scope(
    session: AsyncSession,
    run: AgentRun,
    *,
    result_payload: dict | None = None,
    error_detail: str | None = None,
) -> RuntimeStateScope:
    skill_run_id = await session.scalar(
        select(SkillRun.id).where(SkillRun.run_id == run.id).order_by(SkillRun.id.desc()).limit(1)
    )
    request_payload = dict(run.request_payload or {})
    account_id = request_payload.get("account_id")
    project_id = request_payload.get("project_id")
    return RuntimeStateScope(
        run_id=run.id,
        turn_id=run.turn_id,
        skill_run_id=skill_run_id,
        task_id=run.task_id,
        account_id=account_id if isinstance(account_id, int) else None,
        project_id=project_id if isinstance(project_id, int) else None,
        result_payload=result_payload,
        error_detail=error_detail,
    )
