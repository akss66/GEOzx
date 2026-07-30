"""Runtime worker terminal-failure behavior."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from arq import Retry
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.core.runtime_failures import FailureDisposition, classify_runtime_failure
from app.models import (
    Account,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Event,
    SkillRun,
    TaskBrief,
    ToolExecutionAttempt,
)
from app.models.enums import BrainTaskStatus, BrainTaskType, Platform
from app.orchestrator.agent_harness import AgentHarnessError
from app.orchestrator.skills.account_inspection import ACCOUNT_INSPECTION_SKILL
from app.schemas.brain import IntentDecision
from app.schemas.conversation import (
    CreateConversationTurnRequest,
    TurnExecutionMode,
    TurnExecutionResult,
    TurnRouteDecision,
)
from app.services.agent_runs import claim_agent_run, mark_agent_run_queued
from app.worker import execute_agent_run, recover_agent_runs


@pytest.mark.asyncio
async def test_worker_executes_a_task_free_conversation_before_legacy_task_lookup(
    session,
    admin,
    monkeypatch,
) -> None:
    account = Account(
        org_id=admin.org_id,
        nickname="Task-free worker account",
        platform=Platform.DOUYIN,
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="Task-free worker thread",
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="task-free-worker-1",
        user_input="你好",
    )
    session.add(turn)
    await session.commit()
    await session.refresh(turn)
    request_payload = {
        "account_id": account.id,
        "attachment_ids": [17],
        "client_message_id": turn.client_message_id,
        "execution_preference": "AUTO",
        "message": turn.user_input,
        "requested_skill_code": None,
        "thread_id": thread.id,
        "turn_id": turn.id,
    }
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id=turn.client_message_id,
        request_payload=request_payload,
        thread_id=thread.id,
        turn_id=turn.id,
    )
    await mark_agent_run_queued(
        session,
        run.id,
        task_id=None,
        request_payload=request_payload,
    )
    admin_id = admin.id
    turn_id = turn.id
    run_id = run.id
    client_message_id = turn.client_message_id
    user_input = turn.user_input
    captured: list[
        tuple[int, int, int, CreateConversationTurnRequest, SkillRun | None]
    ] = []

    @asynccontextmanager
    async def test_session_factory():
        yield session

    async def capture_conversation(
        _session,
        user,
        loaded_turn,
        loaded_run,
        request,
        *,
        execution_owner,
        resume_skill_run=None,
    ):
        assert execution_owner == "conversation-worker"
        captured.append(
            (
                user.id,
                loaded_turn.id,
                loaded_run.id,
                request,
                resume_skill_run,
            )
        )
        loaded_run.status = "completed"
        loaded_run.phase = "completed"
        await _session.commit()
        return TurnExecutionResult(
            mode=TurnExecutionMode.ANSWER,
            status="completed",
            response="你好",
        )

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    monkeypatch.setattr(
        "app.worker.execute_conversation_turn",
        capture_conversation,
    )

    result = await execute_agent_run(
        {"worker_id": "conversation-worker"},
        run.id,
    )

    assert result is None
    assert captured == [
        (
            admin_id,
            turn_id,
            run_id,
            CreateConversationTurnRequest(
                client_message_id=client_message_id,
                message=user_input,
                attachment_ids=[17],
            ),
            None,
        )
    ]
    await session.refresh(run)
    assert run.status == "completed"
    assert run.error_code is None


@pytest.mark.parametrize(
    (
        "skill_status",
        "has_ambiguous_call",
        "skill_is_published",
        "expected_run_status",
        "expected_error",
    ),
    [
        (
            "running",
            True,
            True,
            "stopped",
            "TOOL_RESULT_AMBIGUOUS",
        ),
        (
            "completed",
            False,
            True,
            "completed",
            None,
        ),
        (
            "completed",
            False,
            False,
            "completed",
            None,
        ),
    ],
)
@pytest.mark.asyncio
async def test_worker_recovers_expired_v2_skill_without_replaying_side_effects(
    session,
    admin,
    monkeypatch,
    skill_status,
    has_ambiguous_call,
    skill_is_published,
    expected_run_status,
    expected_error,
) -> None:
    account = Account(
        org_id=admin.org_id,
        nickname="Expired Skill worker account",
        platform=Platform.DOUYIN,
        auth={"auth_status": "authorized", "data_sync_status": "ready"},
    )
    session.add(account)
    await session.flush()
    thread = ConversationThread(
        org_id=admin.org_id,
        created_by_id=admin.id,
        account_id=account.id,
        title="Expired Skill worker thread",
    )
    session.add(thread)
    await session.flush()
    turn = ConversationTurn(
        thread_id=thread.id,
        org_id=admin.org_id,
        created_by_id=admin.id,
        client_message_id="expired-v2-skill",
        user_input="Run account inspection",
    )
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title="Expired Skill worker content",
    )
    session.add_all([turn, content])
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title="Expired Skill worker task",
        type=BrainTaskType.ACCOUNT_DIAGNOSIS,
        status=(
            BrainTaskStatus.RUNNING
            if skill_status == "running"
            else BrainTaskStatus.COMPLETED
        ),
        runtime_mode="skill",
    )
    session.add(task)
    await session.flush()
    expired_at = datetime.now(UTC) - timedelta(seconds=1)
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=turn.id,
        task_id=task.id,
        client_message_id=turn.client_message_id,
        status="running",
        phase="skill_runtime",
        lease_owner="crashed-request",
        leased_until=expired_at,
        heartbeat_at=expired_at,
        request_payload={
            "account_id": account.id,
            "attachment_ids": [],
            "client_message_id": turn.client_message_id,
            "execution_preference": "FORMAL_TASK",
            "message": turn.user_input,
            "requested_skill_code": (
                None if has_ambiguous_call else "account_inspection"
            ),
            "thread_id": thread.id,
            "turn_id": turn.id,
        },
    )
    session.add(run)
    await session.flush()
    skill_run = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=(
            f"skill:account_inspection:v{ACCOUNT_INSPECTION_SKILL.version}"
        ),
        skill_code="account_inspection",
        skill_version=ACCOUNT_INSPECTION_SKILL.version,
        status=skill_status,
        input_snapshot={"account_id": account.id, "days": 30},
        output_snapshot=(
            {}
            if skill_status == "running"
            else {
                "status": "completed",
                "task_id": task.id,
                "artifact_id": None,
                "artifact_type": "account_inspection_report",
                "report": {"summary": "Completed before request crash"},
                "response": "账号体检已完成",
                "error_code": None,
            }
        ),
    )
    session.add(skill_run)
    await session.flush()
    tool_call = None
    if has_ambiguous_call:
        tool_call = AgentToolCall(
            org_id=admin.org_id,
            task_id=task.id,
            skill_run_id=skill_run.id,
            thread_id=thread.id,
            turn_id=turn.id,
            tool_code="account.profile",
            tool_name="Account profile",
            idempotency_key=f"{skill_run.id}:account.profile",
            side_effect_level="non_idempotent_write",
            status="running",
            meta={"arguments": {}},
        )
        session.add(tool_call)
        await session.flush()
        session.add(
            ToolExecutionAttempt(
                tool_call_id=tool_call.id,
                attempt_no=1,
                status="dispatched",
            )
        )
    await session.commit()

    @asynccontextmanager
    async def test_session_factory():
        yield session

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    if not skill_is_published:
        from app.orchestrator.skills.public_catalog import PUBLIC_SKILL_POLICIES

        monkeypatch.delitem(PUBLIC_SKILL_POLICIES, "account_inspection")

        class UnexpectedRouteRegistry:
            def get(self, _skill_code):
                raise AssertionError(
                    "persisted SkillRun recovery consulted the current route registry"
                )

        monkeypatch.setattr(
            "app.services.turn_execution.skill_registry",
            UnexpectedRouteRegistry(),
        )

    result = await execute_agent_run({"worker_id": "recovery-worker"}, run.id)

    await session.refresh(run)
    await session.refresh(turn)
    await session.refresh(task)
    await session.refresh(skill_run)
    if tool_call is not None:
        await session.refresh(tool_call)
    assert result == task.id
    assert run.status == expected_run_status
    assert run.error_code == expected_error
    assert turn.assistant_response
    if has_ambiguous_call:
        assert task.status is BrainTaskStatus.PENDING_CONFIRMATION
        assert skill_run.status == "stopped"
        assert skill_run.error_code == "TOOL_RESULT_AMBIGUOUS"
        assert tool_call is not None
        assert tool_call.status == "ambiguous"
        assert tool_call.error == "TOOL_RESULT_AMBIGUOUS"
    else:
        assert task.status is BrainTaskStatus.COMPLETED
        assert skill_run.status == "completed"
        assert skill_run.error_code is None


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
async def test_worker_keeps_ambiguous_post_graph_run_paused_and_unrecoverable(
    session,
    admin,
    monkeypatch,
) -> None:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Ambiguous post-graph task",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
        current_focus="执行外部写操作",
        runtime_mode="langgraph",
    )
    session.add(task)
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="ambiguous-post-graph-worker",
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
                intent="workflow",
                confidence=1,
                reason="execute provider write",
                suggested_expert_codes=[],
                requires_account_context=False,
            ).model_dump(mode="json"),
        },
    )

    @asynccontextmanager
    async def test_session_factory():
        yield session

    async def finish_graph_with_ambiguous_tool(runtime_session, runtime_task, **_kwargs):
        runtime_task.status = BrainTaskStatus.PENDING_CONFIRMATION
        runtime_task.current_focus = "外部操作结果待确认"
        runtime_session.add_all(
            [
                Event(
                    type="brain.runtime.started",
                    payload={
                        "task_id": runtime_task.id,
                        "client_message_id": run.client_message_id,
                    },
                ),
                Event(
                    type="brain.runtime.tool_ambiguous",
                    payload={
                        "task_id": runtime_task.id,
                        "tool_call_id": 91,
                        "error_code": "TOOL_RESULT_AMBIGUOUS",
                    },
                ),
            ]
        )
        await runtime_session.commit()

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    monkeypatch.setattr(
        "app.worker.runtime_graph.start_routed",
        finish_graph_with_ambiguous_tool,
    )

    result = await execute_agent_run({"worker_id": "ambiguous-worker"}, run.id)

    await session.refresh(run)
    await session.refresh(task)
    assert result == task.id
    assert run.status == "waiting_user"
    assert run.phase == "waiting_user"
    assert run.next_retry_at is None
    assert run.lease_owner is None
    assert run.leased_until is None
    assert task.status is BrainTaskStatus.PENDING_CONFIRMATION

    jobs: list[tuple] = []

    class FakePool:
        async def enqueue_job(self, *args, **kwargs):
            jobs.append((*args, kwargs["_job_id"]))
            return object()

    recovered = await recover_agent_runs({"redis": FakePool()})

    assert recovered == 0
    assert jobs == []


@pytest.mark.asyncio
async def test_worker_preserves_legacy_positioning_hint_on_compatibility_route(
    session,
    admin,
    monkeypatch,
) -> None:
    account = Account(
        org_id=admin.org_id,
        nickname="Legacy positioning worker account",
        platform=Platform.DOUYIN,
        auth={"auth_status": "authorized"},
    )
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Legacy positioning worker task",
        type=BrainTaskType.ACCOUNT_DIAGNOSIS,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
    )
    task.brief = TaskBrief(
        goal="positioning diagnosis only",
        platforms=[Platform.DOUYIN.value],
        account_ids=[],
        cycle="current",
        content_goal="positioning diagnosis",
        risk_constraints=[],
        expected_outputs=["positioning diagnosis"],
        confirmation_actions=[],
    )
    session.add_all([account, task])
    await session.flush()
    task.brief.account_ids = [account.id]
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="legacy-positioning-worker",
        request_payload={},
    )
    intent_payload = IntentDecision(
        intent="analysis",
        confidence=0.98,
        reason="positioning diagnosis only",
        suggested_expert_codes=["01-positioning"],
        requires_account_context=True,
    ).model_dump(mode="json")
    await mark_agent_run_queued(
        session,
        run.id,
        task_id=task.id,
        request_payload={
            "operation": "start",
            "task_id": task.id,
            "intent": intent_payload,
        },
    )
    captured: list[tuple[IntentDecision, TurnRouteDecision]] = []

    @asynccontextmanager
    async def test_session_factory():
        yield session

    async def capture_routed_start(
        _session,
        _task,
        *,
        intent,
        route_decision,
        **_kwargs,
    ):
        captured.append((intent, route_decision))

    monkeypatch.setattr("app.worker.async_session", test_session_factory)
    monkeypatch.setattr(
        "app.worker.runtime_graph.start_routed",
        capture_routed_start,
    )

    result = await execute_agent_run({"worker_id": "test-worker"}, run.id)

    assert result == task.id
    assert len(captured) == 1
    captured_intent, captured_route = captured[0]
    assert captured_intent.suggested_expert_codes == ["01-positioning"]
    assert captured_route.mode is TurnExecutionMode.SKILL
    assert captured_route.skill_code == "account_positioning_diagnosis"


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


def test_classify_runtime_failure_keeps_permission_and_input_errors_terminal() -> None:
    with pytest.raises(ValidationError) as invalid_request:
        CreateConversationTurnRequest(
            client_message_id="invalid-request",
            message="",
        )

    failures = [
        HTTPException(status_code=403, detail="forbidden"),
        PermissionError("scope mismatch"),
        invalid_request.value,
    ]

    assert {
        classify_runtime_failure(failure)
        for failure in failures
    } == {FailureDisposition.TERMINAL}
