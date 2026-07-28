"""Runtime worker terminal-failure behavior."""

from contextlib import asynccontextmanager

import pytest
from arq import Retry
from fastapi import HTTPException
from sqlalchemy import select

from app.core.runtime_failures import FailureDisposition, classify_runtime_failure
from app.models import BrainTask, Event
from app.models.enums import BrainTaskStatus, BrainTaskType
from app.orchestrator.agent_harness import AgentHarnessError
from app.schemas.brain import IntentDecision
from app.schemas.conversation import TurnExecutionMode, TurnRouteDecision
from app.services.agent_runs import claim_agent_run, mark_agent_run_queued
from app.worker import execute_agent_run


@pytest.mark.asyncio
async def test_worker_validates_persisted_route_and_passes_it_to_routed_start(
    session,
    admin,
    monkeypatch,
) -> None:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Persisted route task",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
    )
    session.add(task)
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="persisted-route-worker",
        request_payload={},
    )
    route_payload = {
        "mode": "skill",
        "intent": "account_positioning_diagnosis",
        "confidence": 1,
        "reason": "diagnosis only",
        "skill_code": "account_positioning_diagnosis",
        "requires_account_context": True,
        "requires_operation_task": True,
    }
    await mark_agent_run_queued(
        session,
        run.id,
        task_id=task.id,
        request_payload={
            "operation": "start",
            "task_id": task.id,
            "intent": IntentDecision(
                intent="workflow",
                confidence=1,
                reason="diagnosis only",
                suggested_expert_codes=["01-positioning"],
                requires_account_context=True,
            ).model_dump(mode="json"),
            "route_decision": route_payload,
        },
    )
    captured_routes: list[TurnRouteDecision] = []

    @asynccontextmanager
    async def test_session_factory():
        yield session

    async def capture_routed_start(
        _session,
        _task,
        *,
        route_decision,
        **_kwargs,
    ):
        captured_routes.append(route_decision)

    async def reject_legacy_start(*_args, **_kwargs):
        raise AssertionError("worker must use start_routed")

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    monkeypatch.setattr(
        "app.worker.runtime_graph.start_routed",
        capture_routed_start,
        raising=False,
    )
    monkeypatch.setattr(
        "app.worker.runtime_graph.start_smart",
        reject_legacy_start,
    )

    result = await execute_agent_run({"worker_id": "test-worker"}, run.id)

    assert result == task.id
    assert captured_routes == [TurnRouteDecision.model_validate(route_payload)]
    assert captured_routes[0].mode is TurnExecutionMode.SKILL


@pytest.mark.asyncio
async def test_worker_409_conflict_finishes_run_and_task_once(session, admin, monkeypatch) -> None:
    """A business conflict must not schedule another worker attempt."""

    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Conflict task",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
        current_focus="处理中",
        runtime_mode="langgraph",
    )
    session.add(task)
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="conflict-run-409",
        request_payload={},
    )
    await mark_agent_run_queued(
        session,
        run.id,
        task_id=task.id,
        request_payload={
            "operation": "start",
            "task_id": task.id,
            "intent": IntentDecision(
                intent="conversation",
                confidence=1,
                reason="test",
                suggested_expert_codes=[],
                requires_account_context=False,
            ).model_dump(mode="json"),
        },
    )

    @asynccontextmanager
    async def test_session_factory():
        yield session

    async def raise_wrapped_conflict(*_args, **_kwargs):
        conflict = HTTPException(
            status_code=409,
            detail="provider-token=should-not-be-persisted",
        )
        wrapped = AgentHarnessError("runtime failed")
        wrapped.__cause__ = conflict
        raise wrapped

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    monkeypatch.setattr("app.worker.runtime_graph.start_routed", raise_wrapped_conflict)

    result = await execute_agent_run({"worker_id": "test-worker"}, run.id)

    await session.refresh(run)
    await session.refresh(task)
    failures = list(
        await session.scalars(
            select(Event).where(Event.type == "brain.runtime.failed")
        )
    )
    assert result is None
    assert run.attempt == 1
    assert run.status == "failed"
    assert run.phase == "failed"
    assert run.next_retry_at is None
    assert run.leased_until is None
    assert task.status == BrainTaskStatus.FAILED
    assert task.current_focus == "任务因业务冲突未能继续，请处理后重试"
    assert len(failures) == 1
    assert failures[0].payload == {
        "task_id": task.id,
        "agent_run_id": run.id,
        "error_code": "runtime.http_409",
        "message": "任务因业务冲突未能继续，请处理后重试",
        "recovery_action": "请刷新任务状态，处理冲突后重新提交。",
    }
    assert "provider-token" not in str(failures[0].payload)


@pytest.mark.parametrize(
    "failure",
    [
        HTTPException(status_code=408, detail="gateway timeout"),
        HTTPException(status_code=429, detail="rate limited"),
        HTTPException(status_code=503, detail="provider unavailable"),
        TimeoutError("upstream timed out"),
        ConnectionError("connection reset"),
    ],
    ids=["408", "429", "5xx", "timeout", "connection"],
)
@pytest.mark.asyncio
async def test_worker_retryable_failures_raise_arq_retry(
    session, admin, monkeypatch, failure
) -> None:
    """Transient failures retain the bounded ARQ retry path."""

    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Retry task",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
    )
    session.add(task)
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id=f"retryable-{type(failure).__name__}-{id(failure)}",
        request_payload={},
    )
    await mark_agent_run_queued(
        session,
        run.id,
        task_id=task.id,
        request_payload={
            "operation": "start",
            "task_id": task.id,
            "intent": IntentDecision(
                intent="conversation",
                confidence=1,
                reason="test",
                suggested_expert_codes=[],
                requires_account_context=False,
            ).model_dump(mode="json"),
        },
    )

    @asynccontextmanager
    async def test_session_factory():
        yield session

    async def raise_retryable(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    monkeypatch.setattr("app.worker.runtime_graph.start_routed", raise_retryable)

    with pytest.raises(Retry):
        await execute_agent_run({"worker_id": "test-worker"}, run.id)

    await session.refresh(run)
    await session.refresh(task)
    assert run.attempt == 1
    assert run.status == "retry_wait"
    assert run.next_retry_at is not None
    assert task.status == BrainTaskStatus.RUNNING


def test_classify_runtime_failure_treats_business_conflicts_as_terminal() -> None:
    """HTTP business conflicts are terminal even when a wrapper forms a cycle."""

    conflict = HTTPException(status_code=409, detail="do-not-return-this-detail")
    wrapped = AgentHarnessError("wrapper")
    wrapped.__cause__ = conflict
    conflict.__context__ = wrapped

    assert classify_runtime_failure(wrapped) is FailureDisposition.TERMINAL
