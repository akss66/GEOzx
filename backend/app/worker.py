"""arq Worker：消费事件队列。

process_event：落 Event 表 → 跑订阅处理器 → Redis pub/sub 广播给 WebSocket。
失败由 arq 重试（max_tries）。
"""

import asyncio
import json
import logging
import socket
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
from arq import Retry, cron
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.events import EVENTS_CHANNEL, dispatch, redis_settings
from app.core.runtime_failures import FailureDisposition, describe_runtime_failure
from app.db import async_session
from app.models import (
    AgentRun,
    AgentToolCall,
    BrainTask,
    ConversationThread,
    ConversationTurn,
    DataImportJob,
    Event,
    SkillRun,
    User,
)
from app.models.enums import ImportJobStatus
from app.orchestrator.brain_runtime import runtime_graph, runtime_status
from app.orchestrator.checkpointing import open_postgres_checkpointer
from app.schemas.brain import (
    BrainMessageRequest,
    IntentDecision,
    route_decision_from_legacy_intent,
)
from app.schemas.conversation import (
    CreateConversationTurnRequest,
    TurnExecutionResult,
    TurnRouteDecision,
)
from app.services.agent_runs import (
    acquire_agent_run,
    cancel_agent_run,
    complete_agent_run,
    enqueue_agent_runtime,
    heartbeat_agent_run,
    promote_next_waiting_agent_run,
    release_agent_run_failure,
)
from app.services.turn_execution import execute_conversation_turn, execute_revision_task_run

log = logging.getLogger("dyflow.worker")


async def process_event(ctx: dict, event: dict[str, Any]) -> int:
    # 1) 落库（事件溯源）
    async with async_session() as session:
        row = Event(
            type=event["type"],
            payload=event.get("payload"),
            content_item_id=event.get("content_item_id"),
            project_id=event.get("project_id"),
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        event_id = row.id

    # 2) 分发给进程内订阅者
    await dispatch(event["type"], event)

    # 3) 经 Redis 广播给 WebSocket 订阅者
    redis: aioredis.Redis = ctx["redis_pub"]
    await redis.publish(EVENTS_CHANNEL, json.dumps({**event, "id": event_id}))

    log.info("已处理事件 #%s type=%s", event_id, event["type"])
    return event_id


async def generate_video(ctx: dict, deliverable_id: int) -> int | None:
    """后台出片任务：真实调 Ark 生成→下载落本地卷→回写交付物→发事件。"""
    from app.core.events import publish_event
    from app.integrations.video_gen.tasks import generate_video_for_deliverable

    async with async_session() as session:
        asset = await generate_video_for_deliverable(session, deliverable_id, emit=publish_event)
        return asset.id if asset else None


async def execute_account_data_import_job(ctx: dict, job_id: int) -> int:
    from app.services.data_import.jobs import process_import_job

    async with async_session() as session:
        job = await process_import_job(session, job_id=job_id)
        return job.id


async def execute_agent_run(
    ctx: dict,
    run_id: int,
) -> int | None:
    """Execute one leased AgentRun and persist retry or terminal state."""

    worker_id = str(
        ctx.get("worker_id") or f"{socket.gethostname()}:{ctx.get('job_id', f'agent-run:{run_id}')}"
    )
    heartbeat_task: asyncio.Task[None] | None = None
    async with async_session() as session:
        run = await acquire_agent_run(
            session,
            run_id,
            worker_id=worker_id,
            lease_seconds=settings.agent_run_lease_seconds,
        )
        if run is None:
            return None
        request = dict(run.request_payload or {})
        task_id = int(request.get("task_id") or run.task_id or 0)
        operation = str(request.get("operation") or "start")
        is_revision_run = operation == "execute_revision"
        is_conversation_run = (
            run.thread_id is not None and run.turn_id is not None and not is_revision_run
        )
        task = (
            None if is_conversation_run else await _load_runtime_task(session, task_id, run.org_id)
        )
        if not is_conversation_run and task is None:
            await release_agent_run_failure(
                session,
                run_id,
                disposition=FailureDisposition.TERMINAL,
                error_code="runtime.task_not_found",
                error_detail="任务不存在，无法继续执行。",
                user_message="任务不存在，无法继续执行。",
                recovery_action="请刷新任务状态后重新提交。",
            )
            return None

        heartbeat_task = asyncio.create_task(_heartbeat_loop(run_id, worker_id))
        try:
            if is_conversation_run:
                result = await _execute_v2_conversation_run(
                    session,
                    run=run,
                    worker_id=worker_id,
                )
                return result.task_id
            assert task is not None

            revision_status: str | None = None
            if operation == "start":
                intent = IntentDecision.model_validate(request.get("intent"))
                persisted_route = request.get("route_decision")
                route_decision = (
                    TurnRouteDecision.model_validate(persisted_route)
                    if persisted_route is not None
                    else intent.route_decision
                    or route_decision_from_legacy_intent(
                        intent,
                        has_account=bool(task.brief and task.brief.account_ids),
                    )
                )
                await runtime_graph.start_routed(
                    session,
                    task,
                    route_decision=route_decision,
                    intent=intent,
                    client_message_id=str(request.get("client_message_id") or ""),
                    agent_run_id=run.id,
                    agent_run_attempt=run.attempt,
                )
            elif operation == "execute_revision":
                revision_status = await execute_revision_task_run(
                    session,
                    run=run,
                    task=task,
                    worker_id=worker_id,
                )
            elif operation == "prepare_and_start":
                from app.api.brain import _execute_brain_message

                user = await session.scalar(
                    select(User).where(
                        User.id == run.requested_by_id,
                        User.org_id == run.org_id,
                    )
                )
                if user is None:
                    raise ValueError("AgentRun requester is no longer available")
                body = BrainMessageRequest.model_validate(
                    {
                        "message": request.get("message"),
                        "client_message_id": request.get("client_message_id"),
                        "task_id": task.id,
                        "project_id": request.get("project_id"),
                        "account_id": request.get("account_id"),
                        "platform": request.get("platform") or "douyin",
                    }
                )
                await _execute_brain_message(
                    body,
                    user,
                    session,
                    agent_run_id=run.id,
                    agent_run_attempt=run.attempt,
                    regeneration_source_event_id=request.get("regeneration_source_event_id"),
                    force_inline=True,
                    user_message_recorded=bool(request.get("user_message_recorded")),
                )
            elif operation == "resume_decision":
                await runtime_graph.resume_after_decision(
                    session,
                    task,
                    decision_id=str(request.get("decision_id") or ""),
                    choice_id=str(request.get("choice_id") or ""),
                    choice_title=str(request.get("choice_title") or ""),
                    record_selection=False,
                    agent_run_id=run.id,
                    agent_run_attempt=run.attempt,
                )
            elif operation == "resume_permission":
                tool_call = await session.scalar(
                    select(AgentToolCall).where(
                        AgentToolCall.id == int(request.get("tool_call_id") or 0),
                        AgentToolCall.task_id == task.id,
                        AgentToolCall.org_id == run.org_id,
                    )
                )
                if tool_call is None:
                    raise ValueError("Approved AgentToolCall is no longer available")
                await runtime_graph.resume_after_permission(
                    session,
                    task,
                    tool_call,
                    bool(request.get("approved")),
                    agent_run_id=run.id,
                    agent_run_attempt=run.attempt,
                )
            else:
                raise ValueError(f"Unsupported AgentRun operation: {operation}")
            status_value = revision_status or await runtime_status(session, task)
            await complete_agent_run(
                session,
                run_id,
                task_id=task.id,
                status=status_value,
            )
            promoted = await promote_next_waiting_agent_run(session, task.id)
            if promoted is not None:
                await enqueue_agent_runtime(run_id=promoted.id)
            return task.id
        except asyncio.CancelledError:
            await cancel_agent_run(session, run_id)
            if not is_conversation_run:
                task = await _load_runtime_task(session, task_id, run.org_id)
            if task is not None:
                await runtime_graph.record_generation_stopped(
                    session,
                    task,
                    client_message_id=str(request.get("client_message_id") or ""),
                )
            return None
        except Exception as exc:  # noqa: BLE001 - persisted and retried by policy
            failure = describe_runtime_failure(exc)
            retryable, retry_delay = await release_agent_run_failure(
                session,
                run_id,
                disposition=failure.disposition,
                error_code=failure.error_code,
                error_detail=failure.message,
                user_message=failure.message,
                recovery_action=failure.recovery_action,
            )
            log.exception("AgentRun #%s failed on worker %s", run_id, worker_id)
            if retryable:
                raise Retry(defer=retry_delay) from exc
            return None
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task


async def _execute_v2_conversation_run(
    session,
    *,
    run: AgentRun,
    worker_id: str,
) -> TurnExecutionResult:
    user = await session.scalar(
        select(User).where(
            User.id == run.requested_by_id,
            User.org_id == run.org_id,
        )
    )
    thread = await session.scalar(
        select(ConversationThread).where(
            ConversationThread.id == run.thread_id,
            ConversationThread.org_id == run.org_id,
        )
    )
    turn = await session.scalar(
        select(ConversationTurn).where(
            ConversationTurn.id == run.turn_id,
            ConversationTurn.thread_id == run.thread_id,
            ConversationTurn.org_id == run.org_id,
        )
    )
    if user is None or thread is None or turn is None:
        raise ValueError("Turn-owned Conversation execution scope is unavailable")

    persisted_skills = list(
        await session.scalars(
            select(SkillRun).where(
                SkillRun.run_id == run.id,
                SkillRun.org_id == run.org_id,
            )
        )
    )
    recoverable_skills = [
        item
        for item in persisted_skills
        if item.status in {"running", "retry_wait", "waiting_permission"}
    ]
    if len(recoverable_skills) > 1:
        raise RuntimeError("SKILL_RECOVERY_AMBIGUOUS")
    recoverable_skill = (
        recoverable_skills[0]
        if recoverable_skills
        else (persisted_skills[0] if len(persisted_skills) == 1 else None)
    )
    if recoverable_skill is not None and (
        recoverable_skill.thread_id != thread.id
        or recoverable_skill.turn_id != turn.id
        or recoverable_skill.org_id != run.org_id
    ):
        raise PermissionError("Turn-owned SkillRun recovery scope does not match")

    payload = dict(run.request_payload or {})
    request = CreateConversationTurnRequest.model_validate(
        {
            "attachment_ids": payload.get("attachment_ids") or [],
            "client_message_id": payload.get("client_message_id"),
            "execution_preference": payload.get("execution_preference") or "AUTO",
            "message": payload.get("message"),
            "requested_skill_code": payload.get("requested_skill_code"),
        }
    )
    return await execute_conversation_turn(
        session,
        user,
        turn,
        run,
        request,
        execution_owner=worker_id,
        resume_skill_run=recoverable_skill,
    )


async def _load_runtime_task(
    session,
    task_id: int,
    org_id: int,
) -> BrainTask | None:
    return await session.scalar(
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


async def _heartbeat_loop(run_id: int, worker_id: str) -> None:
    interval = max(5, settings.agent_run_lease_seconds // 3)
    while True:
        await asyncio.sleep(interval)
        async with async_session() as session:
            renewed = await heartbeat_agent_run(
                session,
                run_id,
                worker_id=worker_id,
                lease_seconds=settings.agent_run_lease_seconds,
            )
        if not renewed:
            return


async def recover_agent_runs(ctx: dict) -> int:
    """Re-enqueue durable runs whose Redis job was lost or whose lease expired."""

    from app.services.agent_runs import utc_now

    now = utc_now()
    async with async_session() as session:
        rows = (
            await session.scalars(
                select(AgentRun)
                .where(
                    (AgentRun.status == "queued")
                    | (
                        (AgentRun.status == "retry_wait")
                        & (AgentRun.next_retry_at.is_not(None))
                        & (AgentRun.next_retry_at <= now)
                    )
                    | (
                        (AgentRun.status == "running")
                        & (AgentRun.leased_until.is_not(None))
                        & (AgentRun.leased_until <= now)
                    )
                )
                .order_by(AgentRun.id)
                .limit(100)
            )
        ).all()

    pool = ctx["redis"]
    bucket = int(now.timestamp() // 30)
    enqueued = 0
    for run in rows:
        job = await pool.enqueue_job(
            "execute_agent_run",
            run.id,
            _job_id=f"agent-run:{run.id}:recovery:{bucket}",
        )
        if job is not None:
            enqueued += 1
    return enqueued


async def recover_account_data_import_jobs(ctx: dict) -> int:
    """Re-enqueue durable import jobs when their Redis message was lost."""

    now = datetime.now(UTC)
    stale_processing_before = now - timedelta(minutes=30)
    async with async_session() as session:
        rows = (
            await session.scalars(
                select(DataImportJob)
                .where(
                    (DataImportJob.status == ImportJobStatus.QUEUED)
                    | (
                        (DataImportJob.status == ImportJobStatus.PROCESSING)
                        & (DataImportJob.updated_at <= stale_processing_before)
                    )
                )
                .order_by(DataImportJob.id)
                .limit(100)
            )
        ).all()

    pool = ctx["redis"]
    bucket = int(now.timestamp() // 30)
    enqueued = 0
    for job in rows:
        recovered = await pool.enqueue_job(
            "execute_account_data_import_job",
            job.id,
            _job_id=f"account-data-import-job:{job.id}:recovery:{bucket}",
        )
        if recovered is not None:
            enqueued += 1
    return enqueued


async def on_startup(ctx: dict) -> None:
    if settings.agent_runtime_async_enabled and not settings.langgraph_checkpoint_enabled:
        raise RuntimeError("Async Agent runtime requires LangGraph PostgreSQL checkpoints")
    ctx["redis_pub"] = aioredis.from_url(settings.redis_url, decode_responses=True)
    if settings.langgraph_checkpoint_enabled:
        checkpointer_context = open_postgres_checkpointer(settings.database_url)
        checkpointer = await checkpointer_context.__aenter__()
        try:
            await runtime_graph.configure_checkpointer(checkpointer)
        except BaseException:
            await checkpointer_context.__aexit__(None, None, None)
            raise
        ctx["langgraph_checkpointer_context"] = checkpointer_context
        ctx["langgraph_checkpointer"] = checkpointer
    # 导入以注册处理器
    import app.core.event_handlers  # noqa: F401


async def on_shutdown(ctx: dict) -> None:
    checkpointer_context = ctx.get("langgraph_checkpointer_context")
    if checkpointer_context is not None:
        await checkpointer_context.__aexit__(None, None, None)
    await ctx["redis_pub"].aclose()


class WorkerSettings:
    functions = [
        process_event,
        generate_video,
        execute_account_data_import_job,
        execute_agent_run,
    ]
    redis_settings = redis_settings()
    on_startup = on_startup
    on_shutdown = on_shutdown
    max_tries = 3
    allow_abort_jobs = True
    cron_jobs = [
        cron(recover_agent_runs, second={0, 30}, run_at_startup=True),
        cron(
            recover_account_data_import_jobs,
            second={0, 30},
            run_at_startup=True,
        ),
    ]
