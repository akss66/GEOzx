"""Durable AgentRun lifecycle and lease semantics."""

from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.runtime_failures import FailureDisposition
from app.models import (
    Account,
    AgentRun,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Event,
    OrchestrationPlan,
    SkillRun,
    TaskBrief,
)
from app.models.enums import AccountStatus, BrainTaskStatus, BrainTaskType, Platform
from app.orchestrator.agent_harness import AgentHarnessError
from app.schemas.brain import IntentDecision
from app.schemas.conversation import TurnExecutionMode, TurnRouteDecision
from app.services.agent_runs import (
    acquire_agent_run,
    claim_agent_run,
    complete_agent_run,
    enqueue_agent_runtime,
    heartbeat_agent_run,
    mark_agent_run_queued,
    promote_next_waiting_agent_run,
    queue_agent_run_behind_task,
    release_agent_run_failure,
    request_agent_run_cancel,
    utc_now,
)
from app.services.runtime_locking import RuntimeLockConflict
from app.worker import execute_agent_run, recover_agent_runs


@pytest.mark.asyncio
async def test_enqueue_agent_runtime_reports_created_and_existing_jobs(
    monkeypatch,
) -> None:
    enqueue_results = [object(), None]
    calls: list[tuple[str, int, str]] = []

    class Pool:
        async def enqueue_job(self, name: str, run_id: int, *, _job_id: str):
            calls.append((name, run_id, _job_id))
            return enqueue_results.pop(0)

    async def get_pool():
        return Pool()

    monkeypatch.setattr("app.core.events.get_arq_pool", get_pool)

    assert await enqueue_agent_runtime(run_id=42) is True
    assert await enqueue_agent_runtime(run_id=42) is False
    assert calls == [
        ("execute_agent_run", 42, "agent-run:42"),
        ("execute_agent_run", 42, "agent-run:42"),
    ]


@pytest.mark.asyncio
async def test_enqueue_agent_runtime_propagates_connection_failure(monkeypatch) -> None:
    class Pool:
        async def enqueue_job(self, *args, **kwargs):
            del args, kwargs
            raise ConnectionError("queue unavailable")

    async def get_pool():
        return Pool()

    monkeypatch.setattr("app.core.events.get_arq_pool", get_pool)

    with pytest.raises(ConnectionError, match="queue unavailable"):
        await enqueue_agent_runtime(run_id=42)


async def _turn_owned_run(session, admin, *, key: str):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=f"account-{key}",
        status=AccountStatus.ACTIVE,
        auth={"auth_status": "manual"},
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title=key,
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id=key,
        user_input=key,
    )
    session.add(turn)
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id=key,
        request_payload={"message": key, "account_id": account.id},
        thread_id=thread.id,
        turn_id=turn.id,
    )
    return account, thread, turn, run


@pytest.mark.asyncio
async def test_complete_agent_run_rebind_flushes_only_after_root_gate_and_reuses_token(
    session, admin, monkeypatch
) -> None:
    account, _thread, _turn, run = await _turn_owned_run(
        session, admin, key="complete-run-first-rebind"
    )
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title="complete-run-first-content",
    )
    session.add(content)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title="complete-run-first-task",
        status=BrainTaskStatus.RUNNING,
    )
    session.add(task)
    await session.commit()
    import app.services.agent_runs as agent_runs_module

    original_lock = agent_runs_module.lock_runtime_root_scope
    original_close = agent_runs_module.close_runtime_state
    original_flush = session.flush
    root_gate_acquired = False
    observed_prelocked = []

    async def observed_lock(*args, **kwargs):
        nonlocal root_gate_acquired
        token = await original_lock(*args, **kwargs)
        root_gate_acquired = True
        return token

    async def guarded_flush(*args, **kwargs):
        assert root_gate_acquired, "AgentRun rebind flushed before the Run root gate"
        return await original_flush(*args, **kwargs)

    async def observed_close(*args, **kwargs):
        observed_prelocked.append(kwargs.get("prelocked"))
        return await original_close(*args, **kwargs)

    monkeypatch.setattr(agent_runs_module, "lock_runtime_root_scope", observed_lock)
    monkeypatch.setattr(agent_runs_module, "close_runtime_state", observed_close)
    monkeypatch.setattr(session, "flush", guarded_flush)

    await complete_agent_run(session, run.id, task_id=task.id, status="completed")

    assert run.task_id == task.id
    assert observed_prelocked and observed_prelocked[0] is not None


@pytest.mark.asyncio
async def test_complete_agent_run_rebind_rejects_existing_runtime_family(
    session, admin
) -> None:
    _account, thread, turn, run = await _turn_owned_run(
        session, admin, key="complete-rebind-existing-family"
    )
    current_task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="current-task",
        status=BrainTaskStatus.RUNNING,
    )
    target_task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="target-task",
        status=BrainTaskStatus.RUNNING,
    )
    session.add_all([current_task, target_task])
    await session.flush()
    run.task_id = current_task.id
    session.add(
        SkillRun(
            org_id=admin.org_id,
            thread_id=thread.id,
            turn_id=turn.id,
            run_id=run.id,
            task_id=current_task.id,
            idempotency_key="complete-rebind-existing-family",
            skill_code="account_inspection",
            skill_version=1,
            status="running",
            input_snapshot={},
            output_snapshot={},
        )
    )
    await session.commit()

    with pytest.raises(RuntimeLockConflict, match="cannot rebind existing runtime family"):
        await complete_agent_run(
            session,
            run.id,
            task_id=target_task.id,
            status="completed",
        )

    await session.refresh(run)
    assert run.task_id == current_task.id


@pytest.mark.asyncio
async def test_agent_run_lease_blocks_concurrent_worker_and_allows_expired_takeover(
    session, admin
) -> None:
    run, claimed = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="lease-run-1",
        request_payload={"message": "test"},
    )
    assert claimed is True
    await mark_agent_run_queued(session, run.id, task_id=None)

    first = await acquire_agent_run(
        session,
        run.id,
        worker_id="worker-a",
        lease_seconds=60,
    )
    assert first is not None
    assert first.status == "running"
    assert first.attempt == 1

    blocked = await acquire_agent_run(
        session,
        run.id,
        worker_id="worker-b",
        lease_seconds=60,
    )
    assert blocked is None

    first.leased_until = utc_now() - timedelta(seconds=1)
    await session.commit()
    takeover = await acquire_agent_run(
        session,
        run.id,
        worker_id="worker-b",
        lease_seconds=60,
    )
    assert takeover is not None
    assert takeover.lease_owner == "worker-b"
    assert takeover.attempt == 2


@pytest.mark.asyncio
async def test_stale_session_cannot_heartbeat_after_an_expired_lease_is_taken_over(
    session,
    admin,
) -> None:
    run, claimed = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="lease-heartbeat-fence",
        request_payload={"message": "test"},
    )
    assert claimed is True
    await mark_agent_run_queued(session, run.id, task_id=None)
    maker = async_sessionmaker(session.bind, expire_on_commit=False)

    async with maker() as stale_session, maker() as takeover_session:
        stale_run = await acquire_agent_run(
            stale_session,
            run.id,
            worker_id="worker-a",
            lease_seconds=60,
        )
        assert stale_run is not None
        stale_run.leased_until = utc_now() - timedelta(seconds=1)
        await stale_session.commit()

        takeover = await acquire_agent_run(
            takeover_session,
            run.id,
            worker_id="worker-b",
            lease_seconds=60,
        )
        assert takeover is not None
        assert takeover.lease_owner == "worker-b"

        renewed = await heartbeat_agent_run(
            stale_session,
            run.id,
            worker_id="worker-a",
            lease_seconds=60,
        )

        assert renewed is False
        await takeover_session.refresh(takeover)
        assert takeover.lease_owner == "worker-b"


@pytest.mark.asyncio
async def test_agent_run_failure_retries_then_moves_to_dead_letter(session, admin) -> None:
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="retry-run-1",
        request_payload={"message": "test"},
    )
    run.max_attempts = 2
    await mark_agent_run_queued(session, run.id, task_id=None)

    await acquire_agent_run(session, run.id, worker_id="worker-a", lease_seconds=60)
    retryable, retry_delay = await release_agent_run_failure(
        session,
        run.id,
        disposition=FailureDisposition.RETRYABLE,
        error_code="TemporaryFailure",
        error_detail="temporary",
    )
    assert retryable is True
    assert retry_delay > 0
    assert run.status == "retry_wait"
    assert run.next_retry_at is not None

    run.next_retry_at = utc_now() - timedelta(seconds=1)
    await session.commit()
    await acquire_agent_run(session, run.id, worker_id="worker-b", lease_seconds=60)
    retryable, retry_delay = await release_agent_run_failure(
        session,
        run.id,
        disposition=FailureDisposition.RETRYABLE,
        error_code="PermanentFailure",
        error_detail="still failing",
    )
    assert retryable is False
    assert retry_delay == 0
    assert run.status == "dead_letter"
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_turn_owned_retry_exhaustion_projects_public_terminal_event(
    session,
    admin,
) -> None:
    _account, _thread, turn, run = await _turn_owned_run(
        session, admin, key="turn-owned-retry-exhaustion"
    )
    run.max_attempts = 1
    await mark_agent_run_queued(session, run.id, task_id=None)
    await acquire_agent_run(session, run.id, worker_id="worker-a", lease_seconds=60)

    retryable, retry_delay = await release_agent_run_failure(
        session,
        run.id,
        disposition=FailureDisposition.RETRYABLE,
        error_code="TemporaryFailure",
        error_detail="retry budget exhausted",
    )

    assert (retryable, retry_delay) == (False, 0)
    terminal_events = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == turn.id,
                Event.type.in_({"turn.failed", "turn.cancelled", "turn.completed"}),
            )
        )
    )
    assert [event.type for event in terminal_events] == ["turn.failed"]


@pytest.mark.asyncio
async def test_terminal_failure_finalization_is_idempotent_and_closes_task(
    session, admin
) -> None:
    """Repeating terminal finalization cannot duplicate the runtime failure event."""

    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Terminal task",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
        progress=67,
        current_focus="处理中",
    )
    session.add(task)
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="terminal-finalize-once",
        request_payload={},
    )
    await mark_agent_run_queued(session, run.id, task_id=task.id)
    await acquire_agent_run(session, run.id, worker_id="worker-a", lease_seconds=60)

    first = await release_agent_run_failure(
        session,
        run.id,
        disposition=FailureDisposition.TERMINAL,
        error_code="runtime.http_409",
        error_detail="任务因业务冲突未能继续，请处理后重试",
        user_message="任务因业务冲突未能继续，请处理后重试",
        recovery_action="请刷新任务状态，处理冲突后重新提交。",
    )
    second = await release_agent_run_failure(
        session,
        run.id,
        disposition=FailureDisposition.TERMINAL,
        error_code="runtime.http_409",
        error_detail="任务因业务冲突未能继续，请处理后重试",
        user_message="任务因业务冲突未能继续，请处理后重试",
        recovery_action="请刷新任务状态，处理冲突后重新提交。",
    )

    await session.refresh(run)
    await session.refresh(task)
    failures = list(
        await session.scalars(
            select(Event).where(Event.type == "brain.runtime.failed")
        )
    )
    assert first == (False, 0)
    assert second == (False, 0)
    assert run.status == "failed"
    assert run.next_retry_at is None
    assert run.leased_until is None
    assert task.status == BrainTaskStatus.FAILED
    assert task.progress == 0
    assert len(failures) == 1


@pytest.mark.asyncio
async def test_cancel_requested_run_is_not_acquired(session, admin) -> None:
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="cancel-run-1",
        request_payload={"message": "test"},
    )
    await mark_agent_run_queued(session, run.id, task_id=None)
    await request_agent_run_cancel(session, run.id)

    acquired = await acquire_agent_run(
        session,
        run.id,
        worker_id="worker-a",
        lease_seconds=60,
    )

    assert acquired is None
    assert run.status == "cancelled"
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_turn_owned_cancel_projects_public_terminal_event(session, admin) -> None:
    _account, _thread, turn, run = await _turn_owned_run(
        session, admin, key="turn-owned-cancel"
    )
    await mark_agent_run_queued(session, run.id, task_id=None)
    await request_agent_run_cancel(session, run.id)

    acquired = await acquire_agent_run(
        session,
        run.id,
        worker_id="worker-a",
        lease_seconds=60,
    )

    assert acquired is None
    terminal_events = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == turn.id,
                Event.type.in_({"turn.failed", "turn.cancelled", "turn.completed"}),
            )
        )
    )
    assert [event.type for event in terminal_events] == ["turn.cancelled"]


@pytest.mark.asyncio
async def test_followup_runs_wait_for_the_active_task_run_in_fifo_order(
    session, admin
) -> None:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Serialized task",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
    )
    session.add(task)
    await session.commit()

    first, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="serialized-run-1",
        request_payload={"message": "first"},
    )
    await mark_agent_run_queued(session, first.id, task_id=task.id)
    second, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="serialized-run-2",
        request_payload={"message": "second"},
    )
    third, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="serialized-run-3",
        request_payload={"message": "third"},
    )

    assert await queue_agent_run_behind_task(
        session,
        second.id,
        task_id=task.id,
        request_payload={"operation": "prepare_and_start", "message": "second"},
    )
    assert await queue_agent_run_behind_task(
        session,
        third.id,
        task_id=task.id,
        request_payload={"operation": "prepare_and_start", "message": "third"},
    )
    assert second.status == "waiting_predecessor"
    assert third.status == "waiting_predecessor"
    assert await promote_next_waiting_agent_run(session, task.id) is None

    await complete_agent_run(
        session,
        first.id,
        task_id=task.id,
        status="completed",
    )
    promoted = await promote_next_waiting_agent_run(session, task.id)

    assert promoted is not None
    assert promoted.id == second.id
    assert promoted.status == "queued"
    assert third.status == "waiting_predecessor"


@pytest.mark.asyncio
async def test_worker_executes_queued_agent_run_and_persists_completion(
    session, admin, monkeypatch
) -> None:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Queued task",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
    )
    task.brief = TaskBrief(
        goal="Say hello",
        platforms=["douyin"],
        account_ids=[],
        cycle="current",
        content_goal="conversation",
        risk_constraints=[],
        expected_outputs=[],
        confirmation_actions=[],
    )
    task.plan = OrchestrationPlan(
        summary="conversation",
        steps=[],
        quality_gates=[],
        estimated_cost=Decimal("0"),
        requires_human_confirmation=False,
    )
    session.add(task)
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="worker-run-1",
        request_payload={"message": "hello"},
    )
    await mark_agent_run_queued(session, run.id, task_id=task.id)
    run.request_payload = {
        "operation": "start",
        "task_id": task.id,
        "intent": IntentDecision(
            intent="conversation",
            confidence=1,
            reason="test",
            suggested_expert_codes=[],
            requires_account_context=False,
        ).model_dump(mode="json"),
        "client_message_id": "worker-turn-1",
    }
    await session.commit()
    followup, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="worker-run-2",
        request_payload={"message": "follow-up"},
    )
    assert await queue_agent_run_behind_task(
        session,
        followup.id,
        task_id=task.id,
        request_payload={
            "operation": "prepare_and_start",
            "task_id": task.id,
            "message": "follow-up",
        },
    )

    @asynccontextmanager
    async def test_session_factory():
        yield session

    captured_routes: list[TurnRouteDecision] = []

    async def fake_start_routed(
        runtime_session,
        runtime_task,
        *,
        route_decision,
        intent,
        **kwargs,
    ):
        assert runtime_session is session
        assert runtime_task is task
        assert intent.intent == "conversation"
        captured_routes.append(route_decision)
        runtime_task.status = BrainTaskStatus.COMPLETED
        runtime_task.progress = 100
        await runtime_session.commit()
        return runtime_task

    enqueued: list[int] = []

    async def fake_enqueue(*, run_id: int):
        enqueued.append(run_id)

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    monkeypatch.setattr("app.worker.runtime_graph.start_routed", fake_start_routed)
    monkeypatch.setattr("app.worker.enqueue_agent_runtime", fake_enqueue)
    result = await execute_agent_run({"worker_id": "test-worker"}, run.id)

    await session.refresh(run)
    await session.refresh(followup)
    assert result == task.id
    assert captured_routes == [
        TurnRouteDecision(
            mode=TurnExecutionMode.ANSWER,
            intent="conversation",
            confidence=1,
            reason="test",
            requires_account_context=False,
            requires_operation_task=False,
        )
    ]
    assert run.status == "completed"
    assert run.attempt == 1
    assert run.lease_owner is None
    assert followup.status == "queued"
    assert enqueued == [followup.id]


@pytest.mark.asyncio
async def test_worker_prepares_a_serialized_followup_only_when_it_is_promoted(
    session, admin, monkeypatch
) -> None:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Follow-up task",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
    )
    task.brief = TaskBrief(
        goal="first",
        platforms=["douyin"],
        account_ids=[],
        cycle="current",
        content_goal="conversation",
        risk_constraints=[],
        expected_outputs=[],
        confirmation_actions=[],
    )
    task.plan = OrchestrationPlan(
        summary="conversation",
        steps=[],
        quality_gates=[],
        estimated_cost=Decimal("0"),
        requires_human_confirmation=False,
    )
    session.add(task)
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="promoted-followup",
        request_payload={"message": "second"},
    )
    await mark_agent_run_queued(
        session,
        run.id,
        task_id=task.id,
        request_payload={
            "operation": "prepare_and_start",
            "message": "second",
            "task_id": task.id,
            "project_id": None,
            "account_id": None,
            "platform": "douyin",
            "client_message_id": "promoted-followup",
            "user_message_recorded": True,
        },
    )

    @asynccontextmanager
    async def test_session_factory():
        yield session

    prepared: list[dict] = []
    active_lease_owners: list[str | None] = []

    async def fake_execute(body, user, runtime_session, **kwargs):
        claimed_run = await runtime_session.get(AgentRun, kwargs["agent_run_id"])
        active_lease_owners.append(claimed_run.lease_owner if claimed_run else None)
        prepared.append(
            {
                "message": body.message,
                "user_id": user.id,
                **kwargs,
            }
        )
        task.status = BrainTaskStatus.COMPLETED
        task.progress = 100
        await runtime_session.commit()

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    monkeypatch.setattr("app.api.brain._execute_brain_message", fake_execute)

    result = await execute_agent_run({"worker_id": "test-worker"}, run.id)

    assert result == task.id
    assert prepared == [
        {
            "message": "second",
            "user_id": admin.id,
            "agent_run_id": run.id,
            "agent_run_attempt": 1,
            "regeneration_source_event_id": None,
            "force_inline": True,
            "user_message_recorded": True,
            "execution_owner": "test-worker",
        }
    ]
    assert prepared[0]["execution_owner"] == active_lease_owners[0] == "test-worker"


@pytest.mark.asyncio
async def test_worker_does_not_retry_invalid_model_route_configuration(
    session, admin, monkeypatch
) -> None:
    from app.llm.gateway import LLMError
    from app.services.model_infrastructure import ModelRouteConfigurationError

    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Invalid route",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
    )
    task.brief = TaskBrief(
        goal="Diagnose account",
        platforms=["douyin"],
        account_ids=[],
        cycle="current",
        content_goal="diagnosis",
        risk_constraints=[],
        expected_outputs=[],
        confirmation_actions=[],
    )
    task.plan = OrchestrationPlan(
        summary="diagnosis",
        steps=[],
        quality_gates=[],
        estimated_cost=Decimal("0"),
        requires_human_confirmation=False,
    )
    session.add(task)
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="invalid-route-run-1",
        request_payload={"message": "diagnose"},
    )
    await mark_agent_run_queued(session, run.id, task_id=task.id)
    run.request_payload = {
        "operation": "start",
        "task_id": task.id,
        "intent": IntentDecision(
            intent="analysis",
            confidence=1,
            reason="test",
            suggested_expert_codes=["01-positioning"],
            requires_account_context=False,
        ).model_dump(mode="json"),
        "client_message_id": "invalid-route-turn-1",
    }
    await session.commit()

    @asynccontextmanager
    async def test_session_factory():
        yield session

    captured_routes: list[TurnRouteDecision] = []

    async def fail_with_invalid_route(
        runtime_session,
        runtime_task,
        *,
        route_decision,
        intent,
        **kwargs,
    ):
        assert runtime_session is session
        assert runtime_task is task
        assert intent.intent == "analysis"
        captured_routes.append(route_decision)
        config_error = ModelRouteConfigurationError(
            "model removed-model is not available for provider deepseek"
        )
        llm_error = LLMError("all candidate models failed")
        llm_error.__cause__ = config_error
        harness_error = AgentHarnessError("账号定位专家 execution failed")
        harness_error.__cause__ = llm_error
        raise harness_error

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    monkeypatch.setattr("app.worker.runtime_graph.start_routed", fail_with_invalid_route)

    result = await execute_agent_run({"worker_id": "test-worker"}, run.id)

    await session.refresh(run)
    assert result is None
    assert captured_routes == [
        TurnRouteDecision(
            mode=TurnExecutionMode.QUERY,
            intent="analysis",
            confidence=1,
            reason="test",
            requires_account_context=False,
            requires_operation_task=False,
        )
    ]
    assert run.status == "failed"
    assert run.phase == "failed"
    assert run.attempt == 1
    assert run.next_retry_at is None


@pytest.mark.asyncio
async def test_worker_resumes_a_persisted_decision_run(session, admin, monkeypatch) -> None:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Resume decision",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
        thread_id="brain-task-resume-1",
    )
    task.brief = TaskBrief(
        goal="Choose a strategy",
        platforms=["douyin"],
        account_ids=[],
        cycle="current",
        content_goal="strategy",
        risk_constraints=[],
        expected_outputs=[],
        confirmation_actions=[],
    )
    task.plan = OrchestrationPlan(
        summary="strategy",
        steps=[],
        quality_gates=[],
        estimated_cost=Decimal("0"),
        requires_human_confirmation=False,
    )
    session.add(task)
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="decision:direction-1:authority",
        request_payload={
            "operation": "resume_decision",
            "task_id": task.id,
            "decision_id": "direction-1",
            "choice_id": "authority",
            "choice_title": "Authority",
        },
    )
    await mark_agent_run_queued(session, run.id, task_id=task.id)

    @asynccontextmanager
    async def test_session_factory():
        yield session

    resumed: list[dict] = []

    async def fake_resume(runtime_session, runtime_task, **payload):
        resumed.append(payload)
        runtime_task.status = BrainTaskStatus.COMPLETED
        await runtime_session.commit()
        return runtime_task

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    monkeypatch.setattr("app.worker.runtime_graph.resume_after_decision", fake_resume)

    result = await execute_agent_run({"worker_id": "test-worker"}, run.id)

    assert result == task.id
    assert resumed == [
        {
            "decision_id": "direction-1",
            "choice_id": "authority",
            "choice_title": "Authority",
            "record_selection": False,
            "agent_run_id": run.id,
            "agent_run_attempt": run.attempt,
            "execution_owner": "test-worker",
        }
    ]


@pytest.mark.asyncio
async def test_recovery_requeues_a_durable_run_when_redis_job_was_lost(
    session, admin, monkeypatch
) -> None:
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="lost-queue-job",
        request_payload={"operation": "start", "task_id": 123},
    )
    await mark_agent_run_queued(session, run.id, task_id=None)

    @asynccontextmanager
    async def test_session_factory():
        yield session

    jobs: list[tuple] = []

    class FakePool:
        async def enqueue_job(self, *args, **kwargs):
            jobs.append((*args, kwargs["_job_id"]))
            return object()

    monkeypatch.setattr("app.worker.async_session", test_session_factory)

    count = await recover_agent_runs({"redis": FakePool()})

    assert count == 1
    assert jobs[0][0:2] == ("execute_agent_run", run.id)
    assert str(jobs[0][2]).startswith(f"agent-run:{run.id}:recovery:")
