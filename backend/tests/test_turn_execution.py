"""Route-specific execution contracts for one main-Agent conversation Turn."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    ContentItem,
    ConversationThread,
    ConversationTurn,
    Deliverable,
    Event,
    SkillRun,
    StrategyPlan,
)
from app.models.enums import AccountStatus, BrainTaskStatus, Platform
from app.orchestrator.brain_runtime import runtime_graph
from app.orchestrator.skill_runtime import SkillRuntime
from app.orchestrator.skills.registry import SkillRegistry, skill_registry
from app.schemas.attachment import AttachmentContext
from app.schemas.conversation import (
    CreateConversationTurnRequest,
    TurnExecutionMode,
    TurnExecutionResult,
    TurnRouteDecision,
)
from app.services.runtime_state import RuntimeStateScope, close_runtime_state
from app.services.turn_events import TurnEventScope, append_turn_event
from app.services.turn_execution import execute_conversation_turn


async def _turn_context(session, admin, *, key: str):
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
        user_input=f"message-{key}",
    )
    session.add(turn)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        thread_id=thread.id,
        turn_id=turn.id,
        client_message_id=key,
        status="claimed",
        request_payload={},
    )
    session.add(run)
    await session.commit()
    return account, thread, turn, run


async def _four_ledger_context(session, admin, *, key: str):
    account, thread, turn, run = await _turn_context(session, admin, key=key)
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title=f"task-{key}",
        status=BrainTaskStatus.RUNNING,
        progress=17,
        current_focus="running",
    )
    session.add(task)
    await session.flush()
    run.task_id = task.id
    skill_run = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=f"runtime-state:{key}",
        skill_code="account_inspection",
        skill_version=1,
        status="running",
        input_snapshot={},
        output_snapshot={},
    )
    session.add(skill_run)
    await session.commit()
    return account, thread, turn, run, task, skill_run


@pytest.mark.asyncio
async def test_account_inspection_persists_ordered_public_turn_progress(
    session,
    admin,
    monkeypatch,
) -> None:
    from tests.test_account_inspection_skill import (
        _FakeHarness,
        _FakeTools,
        _PassingCritic,
    )

    account, thread, turn, run = await _turn_context(
        session,
        admin,
        key="durable-account-inspection-progress",
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime",
        SkillRuntime(
            tool_executor=_FakeTools(sufficient=True),
            harness=_FakeHarness(),
            critic=_PassingCritic(),
        ),
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "durable-account-inspection-progress",
            requested_skill_code="account_inspection",
        ),
    )

    reliable_types = {
        "turn.received",
        "step.started",
        "step.completed",
        "step.failed",
        "deliverable.updated",
        "turn.completed",
        "turn.failed",
        "turn.blocked",
        "turn.cancelled",
        "turn.stopped",
    }
    events = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == turn.id, Event.type.in_(reliable_types))
            .order_by(Event.sequence)
        )
    )

    assert result.status == "completed"
    assert [(event.type, event.payload.get("step")) for event in events] == [
        ("turn.received", None),
        ("step.started", "read_data"),
        ("step.completed", "read_data"),
        ("step.started", "specialist_work"),
        ("step.completed", "specialist_work"),
        ("step.started", "quality_review"),
        ("step.completed", "quality_review"),
        ("step.started", "prepare_deliverable"),
        ("deliverable.updated", None),
        ("step.completed", "prepare_deliverable"),
        ("turn.completed", None),
    ]
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert {
        (event.org_id, event.account_id, event.thread_id, event.turn_id, event.run_id)
        for event in events
    } == {(admin.org_id, account.id, thread.id, turn.id, run.id)}

    skill_run = await session.scalar(select(SkillRun).where(SkillRun.run_id == run.id))
    deliverable = await session.get(Deliverable, result.projections[0]["artifact_id"])
    assert skill_run is not None
    assert deliverable is not None
    assert events[0].skill_run_id is None
    assert {event.skill_run_id for event in events[1:]} == {skill_run.id}
    deliverable_event = next(
        event for event in events if event.type == "deliverable.updated"
    )
    assert deliverable_event.payload == {
        "deliverable_id": deliverable.id,
        "deliverable_type": deliverable.type.value,
        "version": deliverable.version,
        "status": deliverable.status.value,
    }

    repeated = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "durable-account-inspection-progress",
            requested_skill_code="account_inspection",
        ),
    )
    repeated_events = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == turn.id, Event.type.in_(reliable_types))
            .order_by(Event.sequence)
        )
    )
    assert repeated == result
    assert [event.id for event in repeated_events] == [event.id for event in events]


@pytest.mark.asyncio
async def test_terminal_turn_event_is_first_writer_wins_across_terminal_types(
    session,
    admin,
) -> None:
    account, thread, turn, run = await _turn_context(
        session,
        admin,
        key="durable-terminal-first-wins",
    )
    scope = TurnEventScope(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
    )

    completed = await append_turn_event(
        session,
        scope,
        "turn.completed",
        {"status": "completed"},
        "terminal",
    )
    late_failure = await append_turn_event(
        session,
        scope,
        "turn.failed",
        {"status": "failed"},
        "terminal",
    )

    assert late_failure.id == completed.id
    assert late_failure.type == "turn.completed"
    with pytest.raises(ValueError, match="reserved for terminal events"):
        await append_turn_event(
            session,
            scope,
            "step.started",
            {"step": "read_data"},
            "terminal",
        )


@pytest.mark.asyncio
async def test_terminal_event_first_writer_wins_across_skill_attribution(
    session,
    admin,
) -> None:
    account, thread, turn, run, _task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="terminal-skill-attribution",
    )
    skill_scope = TurnEventScope(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        skill_run_id=skill_run.id,
    )

    completed = await append_turn_event(
        session,
        skill_scope,
        "turn.completed",
        {"status": "completed"},
        "terminal",
    )
    late_failure = await append_turn_event(
        session,
        replace(skill_scope, skill_run_id=None),
        "turn.failed",
        {"status": "failed"},
        "terminal",
    )

    assert late_failure.id == completed.id
    assert late_failure.type == "turn.completed"
    assert late_failure.skill_run_id == skill_run.id
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "turn.completed",
                        "turn.failed",
                        "turn.blocked",
                        "turn.cancelled",
                        "turn.stopped",
                    }
                ),
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_terminal_logical_key_is_reserved_for_terminal_events(
    session,
    admin,
) -> None:
    account, thread, turn, run = await _turn_context(
        session, admin, key="terminal-key-reserved"
    )

    with pytest.raises(ValueError, match="reserved for terminal events"):
        await append_turn_event(
            session,
            TurnEventScope(
                org_id=admin.org_id,
                account_id=account.id,
                thread_id=thread.id,
                turn_id=turn.id,
                run_id=run.id,
            ),
            "step.started",
            {"step": "read_data"},
            "terminal",
        )


@pytest.mark.asyncio
async def test_nonterminal_events_do_not_replay_across_skill_attribution(
    session,
    admin,
) -> None:
    account, thread, turn, run, _task, skill_run = await _four_ledger_context(
        session, admin, key="nonterminal-skill-scope"
    )
    skill_scope = TurnEventScope(
        org_id=admin.org_id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        skill_run_id=skill_run.id,
    )

    skill_event = await append_turn_event(
        session,
        skill_scope,
        "step.started",
        {"step": "read_data"},
        "step:read_data:attempt:1:started",
    )
    run_event = await append_turn_event(
        session,
        replace(skill_scope, skill_run_id=None),
        "step.started",
        {"step": "read_data"},
        "step:read_data:attempt:1:started",
    )

    assert skill_event.id != run_event.id
    assert skill_event.skill_run_id == skill_run.id
    assert run_event.skill_run_id is None


@pytest.mark.asyncio
async def test_account_inspection_failure_preserves_started_checkpoint_and_fails_step(
    session,
    admin,
    monkeypatch,
) -> None:
    from tests.test_account_inspection_skill import _FakeHarness, _PassingCritic

    class FailingReadTools:
        async def execute(self, **_kwargs):
            raise ValueError("read boundary failed")

    account, thread, turn, run = await _turn_context(
        session,
        admin,
        key="durable-account-inspection-failure",
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime",
        SkillRuntime(
            tool_executor=FailingReadTools(),
            harness=_FakeHarness(),
            critic=_PassingCritic(),
        ),
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "durable-account-inspection-failure",
            requested_skill_code="account_inspection",
        ),
    )
    events = list(
        await session.scalars(
            select(Event)
            .where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "turn.received",
                        "step.started",
                        "step.completed",
                        "step.failed",
                        "deliverable.updated",
                        "turn.failed",
                    }
                ),
            )
            .order_by(Event.sequence)
        )
    )

    assert result.status == "failed"
    assert [(event.type, event.payload.get("step")) for event in events] == [
        ("turn.received", None),
        ("step.started", "read_data"),
        ("step.failed", "read_data"),
        ("turn.failed", None),
    ]
    assert [event.sequence for event in events] == [1, 2, 3, 4]
    assert {event.account_id for event in events} == {account.id}
    assert {event.thread_id for event in events} == {thread.id}
    assert not any(event.type == "step.completed" for event in events)
    assert not any(event.type == "deliverable.updated" for event in events)


@pytest.mark.parametrize(
    "failed_stage",
    ["specialist_work", "quality_review", "prepare_deliverable"],
)
@pytest.mark.asyncio
async def test_account_inspection_records_the_actual_failed_stage(
    session,
    admin,
    monkeypatch,
    failed_stage,
) -> None:
    from tests.test_account_inspection_skill import (
        _FakeHarness,
        _FakeTools,
        _PassingCritic,
    )

    class FailingHarness(_FakeHarness):
        async def execute(self, *args, **kwargs):
            raise ValueError("specialist boundary failed")

    class FailingCritic:
        async def review(self, **_kwargs):
            raise ValueError("quality boundary failed")

    account, _thread, turn, run = await _turn_context(
        session,
        admin,
        key=f"durable-{failed_stage}-failure",
    )
    runtime = SkillRuntime(
        tool_executor=_FakeTools(sufficient=True),
        harness=(FailingHarness() if failed_stage == "specialist_work" else _FakeHarness()),
        critic=(FailingCritic() if failed_stage == "quality_review" else _PassingCritic()),
    )
    monkeypatch.setattr("app.services.turn_execution.skill_runtime", runtime)
    if failed_stage == "prepare_deliverable":

        async def fail_deliverable(*_args, **_kwargs):
            raise ValueError("deliverable boundary failed")

        monkeypatch.setattr(
            "app.orchestrator.skill_runtime.write_runtime_deliverable",
            fail_deliverable,
        )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            f"durable-{failed_stage}-failure",
            requested_skill_code="account_inspection",
        ),
    )
    events = list(
        await session.scalars(
            select(Event)
            .where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "step.started",
                        "step.completed",
                        "step.failed",
                        "deliverable.updated",
                        "turn.failed",
                    }
                ),
            )
            .order_by(Event.sequence)
        )
    )
    failed_events = [event for event in events if event.type == "step.failed"]

    assert result.status == "failed"
    assert len(failed_events) == 1
    assert failed_events[0].payload["step"] == failed_stage
    assert failed_events[0].payload["metadata"] == {"attempt": 1}
    assert not any(
        event.type == "step.completed" and event.payload.get("step") == failed_stage
        for event in events
    )
    assert sum(event.type == "turn.failed" for event in events) == 1
    assert not any(event.type == "deliverable.updated" for event in events)
    assert {event.account_id for event in events} == {account.id}


@pytest.mark.asyncio
async def test_account_inspection_commit_failure_is_attributed_to_quality_stage(
    session,
    admin,
    monkeypatch,
) -> None:
    from tests.test_account_inspection_skill import _FakeHarness, _FakeTools, _PassingCritic

    class CommitFailingCritic(_PassingCritic):
        async def review(self, **kwargs):
            result = await super().review(**kwargs)
            runtime_session = kwargs["session"]
            real_commit = runtime_session.commit

            async def fail_once():
                runtime_session.commit = real_commit
                raise RuntimeError("quality checkpoint commit failed")

            runtime_session.commit = fail_once
            return result

    _account, _thread, turn, run = await _turn_context(
        session, admin, key="quality-checkpoint-commit-failure"
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime",
        SkillRuntime(
            tool_executor=_FakeTools(sufficient=True),
            harness=_FakeHarness(),
            critic=CommitFailingCritic(),
        ),
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "quality-checkpoint-commit-failure",
            requested_skill_code="account_inspection",
        ),
    )
    failed_steps = list(
        await session.scalars(
            select(Event).where(Event.turn_id == turn.id, Event.type == "step.failed")
        )
    )

    assert result.status == "failed"
    assert [(event.payload["step"], event.payload["metadata"]) for event in failed_steps] == [
        ("quality_review", {"attempt": 1})
    ]


@pytest.mark.asyncio
async def test_completed_stage_is_not_released_when_checkpoint_commit_fails(
    monkeypatch,
) -> None:
    from app.orchestrator import skill_runtime as skill_runtime_module

    class FailingSession:
        async def commit(self):
            raise RuntimeError("checkpoint commit failed")

    async def append_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(skill_runtime_module, "_append_skill_step_event", append_noop)
    token = skill_runtime_module._ACTIVE_SKILL_STAGES.set((("quality_review", 1),))
    try:
        with pytest.raises(RuntimeError, match="checkpoint commit failed"):
            await skill_runtime_module._complete_skill_stage(
                FailingSession(),
                scope=object(),
                step_code="quality_review",
                attempt=1,
                commit=True,
            )
        assert skill_runtime_module._ACTIVE_SKILL_STAGES.get() == (
            ("quality_review", 1),
        )
    finally:
        skill_runtime_module._ACTIVE_SKILL_STAGES.reset(token)


@pytest.mark.asyncio
async def test_account_inspection_close_failure_is_attributed_to_deferred_stage(
    session,
    admin,
    monkeypatch,
) -> None:
    from app.orchestrator import skill_runtime as skill_runtime_module
    from tests.test_account_inspection_skill import _FakeHarness, _FakeTools, _PassingCritic

    real_close = skill_runtime_module.close_runtime_state
    failed = False

    async def fail_completed_close_once(*args, **kwargs):
        nonlocal failed
        if kwargs["status"] == "completed" and not failed:
            failed = True
            raise RuntimeError("owning closure failed")
        return await real_close(*args, **kwargs)

    monkeypatch.setattr(skill_runtime_module, "close_runtime_state", fail_completed_close_once)
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="prepare-deferred-close-failure"
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime",
        SkillRuntime(
            tool_executor=_FakeTools(sufficient=True),
            harness=_FakeHarness(),
            critic=_PassingCritic(),
        ),
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "prepare-deferred-close-failure",
            requested_skill_code="account_inspection",
        ),
    )
    failed_steps = list(
        await session.scalars(
            select(Event).where(Event.turn_id == turn.id, Event.type == "step.failed")
        )
    )

    assert result.status == "failed"
    assert [(event.payload["step"], event.payload["metadata"]) for event in failed_steps] == [
        ("prepare_deliverable", {"attempt": 1})
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "runtime_status",
        "expected_turn_status",
        "expected_run_status",
        "expected_skill_status",
        "expected_task_status",
    ),
    [
        (
            "completed",
            "completed",
            "completed",
            "completed",
            BrainTaskStatus.COMPLETED,
        ),
        ("failed", "failed", "failed", "failed", BrainTaskStatus.FAILED),
        (
            "dead_letter",
            "dead_letter",
            "dead_letter",
            "failed",
            BrainTaskStatus.FAILED,
        ),
        (
            "cancelled",
            "cancelled",
            "cancelled",
            "cancelled",
            BrainTaskStatus.FAILED,
        ),
        (
            "waiting_permission",
            "waiting_permission",
            "waiting_permission",
            "waiting_permission",
            BrainTaskStatus.PENDING_CONFIRMATION,
        ),
    ],
)
async def test_close_runtime_state_maps_all_four_ledgers_consistently(
    session,
    admin,
    runtime_status,
    expected_turn_status,
    expected_run_status,
    expected_skill_status,
    expected_task_status,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key=f"state-{runtime_status}",
    )

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            turn_id=turn.id,
            skill_run_id=skill_run.id,
            task_id=task.id,
            account_id=account.id,
            result_payload={"status": runtime_status},
        ),
        status=runtime_status,
        message=f"message-{runtime_status}",
        error_code="TEST_ERROR" if runtime_status != "completed" else None,
    )

    await session.refresh(turn)
    await session.refresh(run)
    await session.refresh(skill_run)
    await session.refresh(task)
    assert turn.status == expected_turn_status
    assert run.status == expected_run_status
    assert skill_run.status == expected_skill_status
    assert task.status == expected_task_status


@pytest.mark.asyncio
async def test_close_runtime_state_retry_wait_does_not_write_terminal_response(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-retry-wait",
    )

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            turn_id=turn.id,
            skill_run_id=skill_run.id,
            task_id=task.id,
            account_id=account.id,
        ),
        status="retry_wait",
        message="本次失败，稍后重试。",
        error_code="TRANSIENT",
    )

    await session.refresh(turn)
    await session.refresh(task)
    assert turn.status == "retry_wait"
    assert turn.assistant_response is None
    assert task.status == BrainTaskStatus.RUNNING
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(
                Event.run_id == run.id,
                Event.type == "brain.runtime.message_done",
            )
        )
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_status", ["failed", "cancelled"])
async def test_close_runtime_state_terminal_message_is_idempotent(
    session,
    admin,
    runtime_status,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key=f"state-idempotent-{runtime_status}",
    )
    scope = RuntimeStateScope(
        run_id=run.id,
        turn_id=turn.id,
        skill_run_id=skill_run.id,
        task_id=task.id,
        account_id=account.id,
        result_payload={
            "status": runtime_status,
            "projections": [{"type": "execution_blocked", "code": "TEST"}],
        },
    )

    await close_runtime_state(
        session,
        scope=scope,
        status=runtime_status,
        message="只允许写入一次的终态消息。",
        error_code="TEST_ERROR",
    )
    await close_runtime_state(
        session,
        scope=scope,
        status=runtime_status,
        message="只允许写入一次的终态消息。",
        error_code="TEST_ERROR",
    )

    await session.refresh(turn)
    assert turn.assistant_response == "只允许写入一次的终态消息。"
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(
                Event.run_id == run.id,
                Event.type == "brain.runtime.message_done",
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_close_runtime_state_records_reconciliation_and_skill_timeout(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-diagnostics",
    )
    run.status = "failed"
    turn.status = "running"
    await session.commit()

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=run.id,
            turn_id=turn.id,
            skill_run_id=skill_run.id,
            task_id=task.id,
            account_id=account.id,
            result_payload={"status": "failed"},
        ),
        status="failed",
        message="终态不一致已自动收口。",
    )

    timeout_account, _thread, timeout_turn, timeout_run, timeout_task, timeout_skill_run = (
        await _four_ledger_context(
            session,
            admin,
            key="state-timeout-diagnostic",
        )
    )
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            run_id=timeout_run.id,
            turn_id=timeout_turn.id,
            skill_run_id=timeout_skill_run.id,
            task_id=timeout_task.id,
            account_id=timeout_account.id,
            result_payload={"status": "failed"},
        ),
        status="failed",
        message="专家阶段超时，任务已安全收口。",
        error_code="EXPERT_STAGE_TIMEOUT",
    )

    events = list(
        await session.scalars(
            select(Event).where(
                Event.run_id.in_({run.id, timeout_run.id}),
                Event.type.in_(
                    {
                        "brain.runtime.terminal_state_reconciled",
                        "brain.runtime.skill_stage_timeout",
                    }
                ),
            )
        )
    )
    assert {event.type for event in events} == {
        "brain.runtime.terminal_state_reconciled",
        "brain.runtime.skill_stage_timeout",
    }
    reconciled = next(
        event for event in events if event.type == "brain.runtime.terminal_state_reconciled"
    )
    assert reconciled.payload["previous_turn_status"] == "running"


@pytest.mark.asyncio
async def test_close_runtime_state_pause_then_complete_delivers_both_messages_once(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-pause-then-complete",
    )
    base_scope = {
        "run_id": run.id,
        "org_id": admin.org_id,
        "thread_id": turn.thread_id,
        "turn_id": turn.id,
        "skill_run_id": skill_run.id,
        "task_id": task.id,
        "account_id": account.id,
    }

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **base_scope,
            result_payload={"status": "waiting_permission"},
        ),
        status="waiting_permission",
        message="请先确认工具授权。",
    )
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **base_scope,
            result_payload={"status": "waiting_permission"},
        ),
        status="waiting_permission",
        message="请先确认工具授权。",
    )
    completed_scope = RuntimeStateScope(
        **base_scope,
        result_payload={"status": "completed"},
    )
    await close_runtime_state(
        session,
        scope=completed_scope,
        status="completed",
        message="授权后任务已完成。",
    )
    await close_runtime_state(
        session,
        scope=completed_scope,
        status="completed",
        message="授权后任务已完成。",
    )

    await session.refresh(turn)
    deliveries = list(
        await session.scalars(
            select(Event)
            .where(
                Event.run_id == run.id,
                Event.type == "brain.runtime.message_done",
            )
            .order_by(Event.id)
        )
    )
    assert [event.payload["content"] for event in deliveries] == [
        "请先确认工具授权。",
        "授权后任务已完成。",
    ]
    paused_events = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == turn.id, Event.type == "turn.paused")
            .order_by(Event.id)
        )
    )
    assert [event.payload for event in paused_events] == [{
        "status": "waiting_permission",
        "message": "请先确认工具授权。",
    }]
    assert turn.status == "completed"
    assert turn.assistant_response == "授权后任务已完成。"


@pytest.mark.asyncio
async def test_close_runtime_state_records_a_second_pause_after_resume_but_replays_each_pause_once(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-distinct-pauses",
    )
    scope = RuntimeStateScope(
        run_id=run.id,
        org_id=admin.org_id,
        thread_id=turn.thread_id,
        turn_id=turn.id,
        skill_run_id=skill_run.id,
        task_id=task.id,
        account_id=account.id,
    )

    await close_runtime_state(
        session,
        scope=scope,
        status="waiting_permission",
        message="Approve the first action.",
    )
    await close_runtime_state(
        session,
        scope=scope,
        status="running",
        message="Continuing after approval.",
    )
    await close_runtime_state(
        session,
        scope=scope,
        status="waiting_permission",
        message="Approve the second action.",
    )
    await close_runtime_state(
        session,
        scope=scope,
        status="waiting_permission",
        message="Approve the second action.",
    )

    pauses = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == turn.id, Event.type == "turn.paused")
            .order_by(Event.sequence.asc(), Event.id.asc())
        )
    )
    assert [(event.sequence, event.payload["message"]) for event in pauses] == [
        (1, "Approve the first action."),
        (2, "Approve the second action."),
    ]


@pytest.mark.asyncio
async def test_close_runtime_state_first_terminal_preserves_skill_snapshot_on_replay(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-terminal-snapshot",
    )
    scope_values = {
        "run_id": run.id,
        "turn_id": turn.id,
        "skill_run_id": skill_run.id,
        "task_id": task.id,
        "account_id": account.id,
    }
    formal_snapshot = {
        "status": "completed",
        "artifact_id": 701,
        "response": "正式成果已生成。",
    }

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **scope_values,
            result_payload={"status": "completed"},
            skill_output_snapshot=formal_snapshot,
        ),
        status="completed",
        message="正式成果已生成。",
    )
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **scope_values,
            result_payload={"status": "failed"},
            skill_output_snapshot={
                "status": "failed",
                "error_code": "LATE_FAILURE",
            },
        ),
        status="failed",
        message="迟到的失败不得覆盖正式成果。",
        error_code="LATE_FAILURE",
    )
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **scope_values,
            result_payload={"status": "completed"},
            skill_output_snapshot={
                "status": "completed",
                "artifact_id": 999,
                "response": "重复重放的不同快照。",
            },
        ),
        status="completed",
        message="重复重放不得覆盖正式成果。",
    )

    await session.refresh(skill_run)
    assert skill_run.status == "completed"
    assert skill_run.output_snapshot == formal_snapshot


@pytest.mark.asyncio
async def test_close_runtime_state_stopped_is_terminal_and_first_writer_wins(
    session,
    admin,
) -> None:
    account, thread, turn, run, task, skill_run = await _four_ledger_context(
        session, admin, key="state-stopped-first-wins"
    )
    scope_values = {
        "run_id": run.id,
        "org_id": admin.org_id,
        "thread_id": thread.id,
        "turn_id": turn.id,
        "skill_run_id": skill_run.id,
        "task_id": task.id,
        "account_id": account.id,
    }

    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **scope_values,
            result_payload={"status": "stopped", "reason": "ambiguous side effect"},
        ),
        status="stopped",
        message="Execution stopped for manual reconciliation.",
        error_code="SIDE_EFFECT_AMBIGUOUS",
    )
    await close_runtime_state(
        session,
        scope=RuntimeStateScope(
            **scope_values,
            result_payload={"status": "completed"},
        ),
        status="completed",
        message="Late completion must not overwrite stopped.",
    )

    await session.refresh(turn)
    await session.refresh(run)
    await session.refresh(skill_run)
    await session.refresh(task)
    assert (turn.status, run.status, skill_run.status, task.status) == (
        "stopped",
        "stopped",
        "stopped",
        BrainTaskStatus.FAILED,
    )
    terminal_events = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == turn.id,
                Event.type.in_(
                    {
                        "turn.completed",
                        "turn.failed",
                        "turn.blocked",
                        "turn.cancelled",
                        "turn.stopped",
                    }
                ),
            )
        )
    )
    assert [event.type for event in terminal_events] == ["turn.stopped"]


@pytest.mark.asyncio
async def test_close_runtime_state_rejects_cross_scope_without_partial_commit(
    session,
    admin,
) -> None:
    account, _thread, turn, run, task, _skill_run = await _four_ledger_context(
        session,
        admin,
        key="state-scope-owner",
    )
    (
        _other_account,
        _other_thread,
        _other_turn,
        _other_run,
        _other_task,
        other_skill_run,
    ) = await _four_ledger_context(
        session,
        admin,
        key="state-scope-other",
    )

    with pytest.raises(ValueError, match="ownership"):
        await close_runtime_state(
            session,
            scope=RuntimeStateScope(
                run_id=run.id,
                turn_id=turn.id,
                skill_run_id=other_skill_run.id,
                task_id=task.id,
                account_id=account.id,
            ),
            status="failed",
            message="不得部分写入。",
            error_code="SCOPE_MISMATCH",
        )

    await session.refresh(turn)
    await session.refresh(run)
    await session.refresh(task)
    assert turn.status == "queued"
    assert turn.assistant_response is None
    assert run.status == "claimed"
    assert task.status == BrainTaskStatus.RUNNING


def _request(
    key: str,
    message: str | None = None,
    *,
    execution_preference: str = "AUTO",
    requested_skill_code: str | None = None,
) -> CreateConversationTurnRequest:
    return CreateConversationTurnRequest(
        client_message_id=key,
        message=message or f"message-{key}",
        execution_preference=execution_preference,
        requested_skill_code=requested_skill_code,
    )


def _decision(mode: TurnExecutionMode, **updates) -> TurnRouteDecision:
    values = {
        "mode": mode,
        "intent": f"{mode.value}_intent",
        "confidence": 0.99,
        "reason": "test route",
        "requires_account_context": mode
        in {
            TurnExecutionMode.QUERY,
            TurnExecutionMode.SKILL,
            TurnExecutionMode.TASK,
            TurnExecutionMode.ACTION,
        },
        "requires_operation_task": mode
        in {
            TurnExecutionMode.SKILL,
            TurnExecutionMode.TASK,
            TurnExecutionMode.ACTION,
        },
    }
    values.update(updates)
    return TurnRouteDecision(**values)


@pytest.mark.asyncio
async def test_explicit_skill_receives_account_scoped_capability_request(
    session,
    admin,
    monkeypatch,
) -> None:
    _account, thread, turn, run = await _turn_context(
        session,
        admin,
        key="typed-capability-request",
    )
    turn.user_input = "规划未来14天的10个选题"
    await session.commit()
    captured = {}

    async def capture_capability_request(*_args, **kwargs):
        captured["request"] = kwargs["capability_request"]
        return TurnExecutionResult(
            mode=TurnExecutionMode.SKILL,
            status="completed",
            response="ok",
        )

    monkeypatch.setattr(
        "app.services.turn_execution._execute_composite_skill",
        capture_capability_request,
    )

    async def resolve_contexts(*_args, **_kwargs):
        return [
            AttachmentContext(
                id=41,
                filename="brief.txt",
                mime_type="text/plain",
                parsed_context={"text": "brief"},
            ),
            AttachmentContext(
                id=43,
                filename="metrics.csv",
                mime_type="text/csv",
                parsed_context={"text": "metric,value"},
            ),
        ]

    monkeypatch.setattr(
        "app.services.turn_execution.resolve_attachment_contexts",
        resolve_contexts,
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        CreateConversationTurnRequest(
            client_message_id="typed-capability-request",
            message="规划未来14天的10个选题",
            requested_skill_code="topic_planning",
            execution_preference="FORMAL_TASK",
            attachment_ids=[41, 41, 43],
        ),
    )

    assert result.status == "completed"
    capability_request = captured["request"]
    assert capability_request.org_id == admin.org_id
    assert capability_request.user_id == admin.id
    assert capability_request.account_id == thread.account_id
    assert capability_request.structured_input == {"days": 14, "topic_count": 10}
    assert capability_request.attachment_ids == [41, 43]
    assert [item.id for item in capability_request.attachment_contexts] == [41, 43]
    assert run.request_payload["structured_input"] == {"days": 14, "topic_count": 10}


@pytest.mark.asyncio
async def test_server_trusted_structured_input_reaches_operation_iteration_runtime(
    session,
    admin,
    monkeypatch,
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session,
        admin,
        key="trusted-operation-iteration",
    )
    run.request_payload = {
        "trusted_structured_input": {
            "confirmed_review_artifact_id": 17,
            "cycle_days": 7,
        }
    }
    await session.commit()
    captured = {}

    async def capture_capability_request(*_args, **kwargs):
        captured["request"] = kwargs["capability_request"]
        return TurnExecutionResult(
            mode=TurnExecutionMode.SKILL,
            status="completed",
            response="ok",
        )

    monkeypatch.setattr(
        "app.services.turn_execution._execute_composite_skill",
        capture_capability_request,
    )
    request = CreateConversationTurnRequest(
        client_message_id="trusted-operation-iteration",
        message=turn.user_input,
        requested_skill_code="operation_iteration",
        execution_preference="FORMAL_TASK",
    )

    result = await execute_conversation_turn(session, admin, turn, run, request)

    assert result.status == "completed"
    assert captured["request"].structured_input == {
        "confirmed_review_artifact_id": 17,
        "cycle_days": 7,
    }


@pytest.mark.asyncio
async def test_answer_turn_stays_task_free(session, admin, monkeypatch) -> None:
    account, thread, turn, run = await _turn_context(session, admin, key="answer-1")
    turn.user_input = "你能做什么？"
    await session.commit()
    answer_calls: list[dict] = []

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
        )

    async def answer(*_args, **kwargs):
        answer_calls.append(kwargs)
        return "我是运营大脑。你可以问我账号数据、内容策划或运营执行问题。"

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.answer_turn", answer)
    result = await execute_conversation_turn(
        session, admin, turn, run, _request("answer-1", "你能做什么？")
    )

    assert result.mode is TurnExecutionMode.ANSWER
    assert result.task_id is None
    assert result.status == "completed"
    assert result.response == "我是运营大脑。你可以问我账号数据、内容策划或运营执行问题。"
    assert turn.assistant_response == result.response
    assert run.status == "completed"
    assert len(answer_calls) == 1
    answer_call = answer_calls[0]
    assert answer_call["operating_context"] == (
        f"当前平台：抖音；当前账号：{account.nickname}（账号 ID {account.id}）；"
        "当前项目：未选择项目。"
    )
    assert answer_call["history"] == []
    assert answer_call["scope"] == {
        "account_id": account.id,
        "thread_id": thread.id,
        "turn_id": turn.id,
    }
    assert callable(answer_call["stream_observer"])
    for model in (BrainTask, StrategyPlan, AgentInvocation, AgentToolCall):
        assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_deterministic_answer_skips_model_classification(session, admin, monkeypatch) -> None:
    """Catches deterministic hits regressing into unnecessary model classification."""

    _account, _thread, turn, run = await _turn_context(session, admin, key="deterministic-greeting")
    turn.user_input = "你好"
    await session.commit()

    async def should_not_classify(*_args, **_kwargs):
        raise AssertionError("deterministic greeting must not classify")

    async def answer(*_args, **_kwargs):
        return "你好，我是运营大脑。"

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn",
        should_not_classify,
    )
    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.answer_turn", answer)

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("deterministic-greeting", "你好"),
    )

    assert result.mode is TurnExecutionMode.ANSWER
    assert result.response == "你好，我是运营大脑。"


@pytest.mark.asyncio
async def test_low_risk_question_uses_only_the_answer_model(session, admin, monkeypatch) -> None:
    _account, _thread, turn, run = await _turn_context(
        session,
        admin,
        key="single-model-answer",
    )
    message = "短视频运营有哪些常见误区？"
    turn.user_input = message
    await session.commit()
    answer_calls = 0

    async def should_not_classify(*_args, **_kwargs):
        raise AssertionError("low-risk questions must not call the classifier")

    async def answer(*_args, **_kwargs):
        nonlocal answer_calls
        answer_calls += 1
        return "常见误区包括目标不清、只追热点和不做数据复盘。"

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn",
        should_not_classify,
    )
    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.answer_turn", answer)

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("single-model-answer", message),
    )

    assert result.mode is TurnExecutionMode.ANSWER
    assert result.status == "completed"
    assert answer_calls == 1


@pytest.mark.asyncio
async def test_answer_turn_streams_provider_deltas_before_persisting_final_turn(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="answer-live-stream")
    turn.user_input = "请实时回答"
    await session.commit()
    published: list[tuple[str, dict]] = []
    response_during_delta: list[str | None] = []

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
        )

    async def answer(*_args, stream_observer=None, **_kwargs):
        assert callable(stream_observer)
        await stream_observer(
            {
                "phase": "start",
                "agent_code": "00-decision",
                "model": "test-model",
            }
        )
        for delta in ("真", "实"):
            await stream_observer(
                {
                    "phase": "delta",
                    "agent_code": "00-decision",
                    "model": "test-model",
                    "delta": delta,
                }
            )
        await stream_observer(
            {
                "phase": "done",
                "agent_code": "00-decision",
                "model": "test-model",
                "content": "真实",
            }
        )
        return "真实"

    async def publish(event_type, payload, **_kwargs):
        published.append((event_type, payload))
        if event_type == "brain.runtime.message_delta":
            response_during_delta.append(turn.assistant_response)

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.answer_turn", answer)
    monkeypatch.setattr("app.orchestrator.brain_runtime.publish_realtime_event", publish)

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("answer-live-stream", "请实时回答"),
    )

    assert result.response == "真实"
    assert response_during_delta == [None, None]
    assert [
        payload["delta"]
        for event_type, payload in published
        if event_type == "brain.runtime.message_delta"
    ] == ["真", "实"]
    assert published[0][0] == "brain.runtime.message_start"
    assert published[-2][0] == "brain.runtime.message_done"
    assert published[-1][0] == "brain.runtime.completed"
    assert (
        await session.scalar(
            select(func.count(Event.id)).where(
                Event.turn_id == turn.id,
                Event.type == "brain.runtime.message_delta",
            )
        )
        == 0
    )


@pytest.mark.asyncio
async def test_clarify_turn_persists_question_without_task(session, admin, monkeypatch) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="clarify-1")

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.CLARIFY,
            requires_account_context=True,
            requires_operation_task=False,
            missing_field="period",
            clarifying_question="你希望查看最近多少天？",
        )

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    result = await execute_conversation_turn(session, admin, turn, run, _request("clarify-1"))

    assert result.mode is TurnExecutionMode.CLARIFY
    assert result.task_id is None
    assert result.response == "你希望查看最近多少天？"
    assert turn.assistant_response == result.response
    assert await session.scalar(select(func.count(BrainTask.id))) == 0


@pytest.mark.asyncio
async def test_query_uses_authorized_account_and_records_one_skill_run(
    session, admin, monkeypatch
) -> None:
    account, thread, turn, run = await _turn_context(session, admin, key="query-1")
    invocations: list[dict] = []

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, name, params, context):
            invocations.append(
                {
                    "name": name,
                    "params": dict(params),
                    "account_id": context.account_id,
                    "task_id": context.task_id,
                }
            )
            return {
                "account_id": context.account_id,
                "period": {
                    "days": params["days"],
                    "start": "2026-06-30",
                    "end": "2026-07-29",
                },
                "metrics": {
                    "play": {
                        "value": 42,
                        "source": "platform_export",
                        "observed_at": "2026-07-18",
                    },
                    "exposure": {"value": None},
                    "content_count": {"value": 3},
                    "follower_count": {"value": 100},
                    "new_followers": {"value": 2},
                    "like_count": {"value": 9},
                    "comment_count": {"value": 4},
                    "cover_click_rate": {"value": None},
                },
                "sources": [
                    {
                        "batch_id": 7,
                        "source_kind": "platform_export",
                        "confirmed_at": "2026-07-20T10:00:00+08:00",
                    }
                ],
                "coverage": {
                    "content_metrics": "available",
                    "audience": "missing",
                },
            }

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("query-1"),
    )

    assert result.mode is TurnExecutionMode.QUERY
    assert result.task_id is None
    assert result.projections[0]["type"] == "account_data"
    assert result.projections[0]["account_id"] == account.id
    assert invocations == [
        {
            "name": "account.data_context",
            "params": {"days": 30},
            "account_id": account.id,
            "task_id": None,
        }
    ]
    skill_run = await session.scalar(select(SkillRun))
    assert skill_run is not None
    assert skill_run.thread_id == thread.id
    assert skill_run.turn_id == turn.id
    assert skill_run.run_id == run.id
    assert skill_run.task_id is None
    assert skill_run.status == "completed"
    assert skill_run.skill_version == 1
    assert skill_run.input_snapshot == {"account_id": account.id, "days": 30}
    assert skill_run.output_snapshot["account_id"] == account.id
    assert "数据周期：2026-06-30 至 2026-07-29（近 30 天）" in result.response
    assert "数据来源：平台导出批次 #7" in result.response
    assert "播放量：42（平台导出，观测于 2026-07-18）" in result.response
    assert "缺失数据：曝光量、封面点击率、受众画像" in result.response
    assert "account_id" not in result.response
    assert "{'" not in result.response
    assert await session.scalar(select(func.count(BrainTask.id))) == 0
    assert await session.scalar(select(func.count(StrategyPlan.id))) == 0
    assert await session.scalar(select(func.count(AgentInvocation.id))) == 0


@pytest.mark.asyncio
async def test_query_explains_pending_import_instead_of_claiming_data_was_read(
    session,
    admin,
    monkeypatch,
) -> None:
    account, _thread, turn, run = await _turn_context(
        session,
        admin,
        key="query-pending-import",
    )

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, _name, _params, context):
            return {
                "account_id": context.account_id,
                "data_status": "pending_import",
                "query_window": {
                    "days": 30,
                    "start": "2026-07-02",
                    "end": "2026-07-31",
                },
                "data_period": None,
                "metrics": {},
                "sources": [],
                "coverage": {},
                "pending_imports": [
                    {
                        "batch_id": 8,
                        "status": "preview_ready",
                        "template_code": "douyin_period_aggregate_v1",
                        "row_count": 1,
                        "period_start": "2026-05-02",
                        "period_end": "2026-07-31",
                    }
                ],
            }

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("query-pending-import"),
    )

    assert result.projections[0]["data"]["data_status"] == "pending_import"
    assert "当前账号暂无已正式写入的可分析数据" in result.response
    assert "待确认导入批次 #8" in result.response
    assert "2026-05-02 至 2026-07-31" in result.response
    assert "请先在数据中心完成校验并正式写入" in result.response
    assert "数据周期：" not in result.response
    assert "已读取当前账号的数据" not in result.response
    assert result.projections[0]["account_id"] == account.id


@pytest.mark.asyncio
async def test_completed_query_duplicate_does_not_reclassify_or_reinvoke(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="query-duplicate")
    calls = 0
    tool_calls = 0

    async def classify(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, _name, _params, context):
            nonlocal tool_calls
            tool_calls += 1
            return {"account_id": context.account_id, "metrics": {}}

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())
    request = _request("query-duplicate")
    first = await execute_conversation_turn(session, admin, turn, run, request)

    async def should_not_classify(*_args, **_kwargs):
        raise AssertionError("terminal duplicate must not classify")

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn",
        should_not_classify,
    )
    repeated = await execute_conversation_turn(session, admin, turn, run, request)

    assert calls == 1
    assert tool_calls == 1
    assert repeated == first
    assert await session.scalar(select(func.count(SkillRun.id))) == 1
    assert await session.scalar(select(func.count(AgentToolCall.id))) == 0


@pytest.mark.asyncio
async def test_query_tool_failure_closes_run_and_skill_without_retry_or_leak(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="query-failure")
    tool_calls = 0

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, *_args, **_kwargs):
            nonlocal tool_calls
            tool_calls += 1
            raise RuntimeError("provider-secret-must-not-leak")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())
    request = _request("query-failure")
    first = await execute_conversation_turn(session, admin, turn, run, request)
    repeated = await execute_conversation_turn(session, admin, turn, run, request)

    skill_run = await session.scalar(select(SkillRun))
    assert first == repeated
    assert first.status == "failed"
    assert first.error_code == "QUERY_TOOL_UNAVAILABLE"
    assert first.projections == []
    assert "provider-secret" not in first.response
    assert skill_run is not None
    assert skill_run.status == "failed"
    assert skill_run.error_code == "QUERY_TOOL_UNAVAILABLE"
    assert run.status == "failed"
    assert run.error_detail is None
    assert turn.assistant_response == first.response
    assert tool_calls == 1
    events = list(await session.scalars(select(Event)))
    assert all("provider-secret" not in str(event.payload) for event in events)


@pytest.mark.asyncio
async def test_query_retryable_infrastructure_failure_bubbles_to_the_worker(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="query-retryable")

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, *_args, **_kwargs):
            raise HTTPException(status_code=503, detail="provider-secret")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())

    with pytest.raises(HTTPException) as caught:
        await execute_conversation_turn(
            session,
            admin,
            turn,
            run,
            _request("query-retryable"),
        )

    assert caught.value.status_code == 503
    skill_run = await session.scalar(select(SkillRun))
    assert skill_run is not None
    assert skill_run.status == "running"
    assert turn.assistant_response is None
    assert run.status == "claimed"


@pytest.mark.parametrize("tool_account_id", [None, 999999])
@pytest.mark.asyncio
async def test_query_rejects_missing_or_cross_account_tool_result(
    session, admin, monkeypatch, tool_account_id
) -> None:
    account, _thread, turn, run = await _turn_context(
        session, admin, key=f"query-scope-{tool_account_id}"
    )

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.QUERY,
            skill_code="account_data_query",
            requires_operation_task=False,
        )

    class Adapter:
        async def invoke(self, *_args, **_kwargs):
            return {
                "account_id": tool_account_id,
                "secret_raw_data": "must-not-project",
            }

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter())
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(f"query-scope-{tool_account_id}"),
    )

    assert result.status == "failed"
    assert result.error_code == "TOOL_RESULT_SCOPE_MISMATCH"
    assert result.projections == []
    assert account.id != tool_account_id
    events = list(await session.scalars(select(Event)))
    assert all("secret_raw_data" not in str(event.payload) for event in events)


@pytest.mark.asyncio
async def test_intelligence_unavailable_is_structured_blocked_not_answer(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="intelligence-down")

    async def classify(*_args, **_kwargs):
        from app.orchestrator.brain_intelligence import IntelligenceUnavailable

        raise IntelligenceUnavailable("raw-provider-failure")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("intelligence-down"),
    )

    assert result.status == "blocked"
    assert result.error_code == "INTELLIGENCE_UNAVAILABLE"
    assert "raw-provider" not in result.response
    assert run.status == "blocked"
    assert run.error_detail is None
    assert await session.scalar(select(func.count(BrainTask.id))) == 0


@pytest.mark.asyncio
async def test_unavailable_skill_is_structured_blocked_without_artifact(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="skill-blocked")

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.SKILL,
            skill_code="not_implemented_skill",
        )

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("skill-blocked"),
    )

    assert result.status == "blocked"
    assert result.error_code == "INTELLIGENCE_UNAVAILABLE"
    assert result.task_id is None
    assert run.status == "blocked"
    assert run.error_code == "INTELLIGENCE_UNAVAILABLE"
    assert await session.scalar(select(func.count(ContentItem.id))) == 0
    assert await session.scalar(select(func.count(BrainTask.id))) == 0


@pytest.mark.asyncio
async def test_unknown_explicit_skill_is_blocked_without_formal_side_effects(
    session,
    admin,
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="explicit-unknown-skill")

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "explicit-unknown-skill",
            requested_skill_code="not_registered",
        ),
    )

    assert result.status == "blocked"
    assert result.error_code == "UNKNOWN_SKILL"
    assert result.task_id is None
    assert result.projections == [
        {
            "type": "execution_blocked",
            "skill_code": "not_registered",
            "code": "UNKNOWN_SKILL",
            "recovery_action": "请从当前公开能力目录重新选择。",
        }
    ]
    assert run.status == "blocked"
    for model in (SkillRun, Deliverable, ContentItem, BrainTask):
        assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_platform_incompatible_explicit_skill_never_reaches_executor(
    session,
    admin,
    monkeypatch,
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="explicit-platform-incompatible"
    )
    definition = replace(
        skill_registry.get("account_inspection"),
        supported_platforms=frozenset({"xiaohongshu"}),
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_registry",
        SkillRegistry([definition]),
        raising=False,
    )

    async def must_not_execute(*_args, **_kwargs):
        raise AssertionError("platform-incompatible Skill must not execute")

    monkeypatch.setattr(
        "app.services.turn_execution.skill_runtime.execute",
        must_not_execute,
    )
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "explicit-platform-incompatible",
            requested_skill_code="account_inspection",
        ),
    )

    assert result.status == "blocked"
    assert result.error_code == "UNSUPPORTED_PLATFORM"
    assert result.task_id is None
    for model in (SkillRun, Deliverable, ContentItem, BrainTask):
        assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_unpublished_explicit_skill_is_blocked_without_skill_run(
    session,
    admin,
    monkeypatch,
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="explicit-unpublished")
    public_definition = skill_registry.get("account_inspection")
    private_definition = replace(
        public_definition,
        code="internal_shadow_skill",
    )
    monkeypatch.setattr(
        "app.services.turn_execution.skill_registry",
        SkillRegistry([public_definition, private_definition]),
        raising=False,
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(
            "explicit-unpublished",
            requested_skill_code="internal_shadow_skill",
        ),
    )

    assert result.status == "blocked"
    assert result.error_code == "UNPUBLISHED_SKILL"
    assert result.task_id is None
    for model in (SkillRun, Deliverable, ContentItem, BrainTask):
        assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.parametrize(
    "classified_mode",
    [
        TurnExecutionMode.SKILL,
        TurnExecutionMode.TASK,
        TurnExecutionMode.ACTION,
    ],
)
@pytest.mark.asyncio
async def test_discuss_only_prevents_workflow_execution(
    session, admin, monkeypatch, classified_mode
) -> None:
    key = f"discuss-{classified_mode.value}"
    _account, _thread, turn, run = await _turn_context(session, admin, key=key)
    started = 0

    async def classify(*_args, **_kwargs):
        values = {}
        if classified_mode is TurnExecutionMode.SKILL:
            values["skill_code"] = "account_inspection"
        return _decision(classified_mode, **values)

    async def should_not_start(*_args, **_kwargs):
        nonlocal started
        started += 1
        raise AssertionError("DISCUSS_ONLY must not start routed work")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", should_not_start)
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request(key, execution_preference="DISCUSS_ONLY"),
    )

    assert result.status == "completed"
    assert result.task_id is None
    assert "未执行" in result.response
    assert started == 0
    assert await session.scalar(select(func.count(BrainTask.id))) == 0
    assert await session.scalar(select(func.count(SkillRun.id))) == 0


@pytest.mark.asyncio
async def test_formal_task_forces_non_clarify_route_into_task(session, admin, monkeypatch) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="formal-task")
    routed_modes: list[TurnExecutionMode] = []

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
        )

    async def start_routed(_session, task, **kwargs):
        routed_modes.append(kwargs["route_decision"].mode)
        task.status = BrainTaskStatus.COMPLETED
        await _session.commit()
        return task

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", start_routed)
    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("formal-task", execution_preference="FORMAL_TASK"),
    )

    assert result.mode is TurnExecutionMode.TASK
    assert result.task_id is not None
    assert routed_modes == [TurnExecutionMode.TASK]


@pytest.mark.asyncio
async def test_migrated_operation_never_enters_legacy_operation_task(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="typed-topic-route"
    )
    message = "给我做7天5个选题"
    turn.user_input = message
    await session.commit()
    captured: list[str] = []

    async def execute_skill(*_args, **kwargs):
        captured.append(kwargs["decision"].skill_code)
        return TurnExecutionResult(
            mode=TurnExecutionMode.SKILL,
            status="completed",
            response="typed skill",
        )

    async def forbidden_legacy(*_args, **_kwargs):
        raise AssertionError("migrated operation must not enter legacy task graph")

    monkeypatch.setattr(
        "app.services.turn_execution._execute_composite_skill", execute_skill
    )
    monkeypatch.setattr(
        "app.services.turn_execution._execute_operation_task", forbidden_legacy
    )

    result = await execute_conversation_turn(
        session,
        admin,
        turn,
        run,
        _request("typed-topic-route", message=message),
    )

    assert result.mode is TurnExecutionMode.SKILL
    assert captured == ["topic_planning"]


@pytest.mark.asyncio
async def test_legacy_runtime_rejects_migrated_operation_intent() -> None:
    decision = _decision(
        TurnExecutionMode.TASK,
        intent="topic_planning",
    )

    with pytest.raises(ValueError, match="MIGRATED_OPERATION_REQUIRES_TYPED_SKILL"):
        await runtime_graph.start_routed(
            None,
            SimpleNamespace(),
            route_decision=decision,
        )


@pytest.mark.parametrize(
    "mode",
    [TurnExecutionMode.TASK, TurnExecutionMode.ACTION],
)
@pytest.mark.asyncio
async def test_strategy_task_creates_exactly_one_task_and_uses_routed_runtime(
    session, admin, monkeypatch, mode
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="task-1")
    started: list[tuple[int, int]] = []

    async def classify(*_args, **_kwargs):
        return _decision(mode)

    async def start_routed(_session, task, **kwargs):
        started.append((task.id, kwargs["agent_run_id"]))
        task.status = BrainTaskStatus.COMPLETED
        task.progress = 100
        task.current_focus = "done"
        await _session.commit()
        return task

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", start_routed)
    request = _request("task-1")
    first = await execute_conversation_turn(session, admin, turn, run, request)
    repeated = await execute_conversation_turn(session, admin, turn, run, request)

    assert first.task_id is not None
    assert repeated == first
    assert run.task_id == first.task_id
    assert started == [(first.task_id, run.id)]
    assert await session.scalar(select(func.count(BrainTask.id))) == 1


@pytest.mark.asyncio
async def test_operation_start_failure_closes_task_run_and_turn_without_replay(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="task-failure")
    starts = 0

    async def classify(*_args, **_kwargs):
        return _decision(TurnExecutionMode.TASK)

    async def start_routed(*_args, **_kwargs):
        nonlocal starts
        starts += 1
        raise RuntimeError("runtime-secret")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", start_routed)
    request = _request("task-failure")
    first = await execute_conversation_turn(session, admin, turn, run, request)
    repeated = await execute_conversation_turn(session, admin, turn, run, request)
    task = await session.get(BrainTask, first.task_id)

    assert first == repeated
    assert first.status == "failed"
    assert first.error_code == "OPERATION_RUNTIME_FAILED"
    assert "runtime-secret" not in first.response
    assert run.status == "failed"
    assert run.error_detail is None
    assert task is not None
    assert task.status == BrainTaskStatus.FAILED
    assert turn.assistant_response == first.response
    assert starts == 1
    events = list(await session.scalars(select(Event)))
    assert all("runtime-secret" not in str(event.payload) for event in events)


@pytest.mark.asyncio
async def test_operation_retryable_infrastructure_failure_bubbles_to_the_worker(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(session, admin, key="task-retryable")

    async def classify(*_args, **_kwargs):
        return _decision(TurnExecutionMode.TASK)

    async def start_routed(*_args, **_kwargs):
        raise HTTPException(status_code=503, detail="runtime-provider-secret")

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", start_routed)

    with pytest.raises(HTTPException) as caught:
        await execute_conversation_turn(
            session,
            admin,
            turn,
            run,
            _request("task-retryable"),
        )

    assert caught.value.status_code == 503
    await session.refresh(run)
    await session.refresh(turn)
    task = await session.get(BrainTask, run.task_id)
    assert task is not None
    assert task.status == BrainTaskStatus.RUNNING
    assert run.status == "claimed"
    assert turn.assistant_response is None


@pytest.mark.parametrize(
    ("runtime_state", "expected_run_status", "expected_error"),
    [
        ("waiting_permission", "waiting_permission", None),
        ("waiting_decision", "waiting_decision", None),
        ("waiting_user", "waiting_user", None),
        ("failed", "failed", "OPERATION_RUNTIME_FAILED"),
        ("stopped", "stopped", "OPERATION_STOPPED"),
    ],
)
@pytest.mark.asyncio
async def test_operation_runtime_state_is_persisted_without_reexecution(
    session,
    admin,
    monkeypatch,
    runtime_state,
    expected_run_status,
    expected_error,
) -> None:
    key = f"task-state-{runtime_state}"
    _account, _thread, turn, run = await _turn_context(session, admin, key=key)
    starts = 0

    async def classify(*_args, **_kwargs):
        return _decision(TurnExecutionMode.TASK)

    async def start_routed(_session, task, **_kwargs):
        nonlocal starts
        starts += 1
        return task

    async def status(*_args, **_kwargs):
        return runtime_state

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.runtime_graph.start_routed", start_routed)
    monkeypatch.setattr("app.services.turn_execution.runtime_status", status)
    request = _request(key)
    first = await execute_conversation_turn(session, admin, turn, run, request)
    repeated = await execute_conversation_turn(session, admin, turn, run, request)
    task = await session.get(BrainTask, first.task_id)

    assert first == repeated
    assert first.status == runtime_state
    assert first.error_code == expected_error
    assert run.status == expected_run_status
    assert starts == 1
    assert task is not None
    if runtime_state in {"failed", "stopped"}:
        assert task.status == BrainTaskStatus.FAILED
    else:
        assert task.status == BrainTaskStatus.PENDING_CONFIRMATION


@pytest.mark.asyncio
async def test_task_free_events_have_turn_lineage_and_publish_after_commit(
    session, admin, monkeypatch
) -> None:
    account, thread, turn, run = await _turn_context(session, admin, key="lineage-1")
    published: list[int] = []

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
        )

    async def answer(*_args, **_kwargs):
        return "这是一个可追踪的流式回复。"

    async def publish(_event_type, _payload, **kwargs):
        event_id = kwargs["event_id"]
        row = await session.get(Event, event_id)
        await session.refresh(turn)
        assert row is not None
        assert turn.assistant_response
        published.append(event_id)

    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.classify_turn", classify)
    monkeypatch.setattr("app.services.turn_execution.brain_intelligence.answer_turn", answer)
    monkeypatch.setattr("app.orchestrator.brain_runtime.publish_realtime_event", publish)
    await execute_conversation_turn(session, admin, turn, run, _request("lineage-1"))

    events = list(
        await session.scalars(
            select(Event).where(Event.type.like("brain.runtime.%")).order_by(Event.id)
        )
    )
    assert set(published) == {event.id for event in events}
    assert len(published) == len(events)
    assert events
    for event in events:
        payload = event.payload
        assert payload["org_id"] == admin.org_id
        assert payload["account_id"] == account.id
        assert payload["thread_id"] == thread.id
        assert payload["turn_id"] == turn.id
        assert payload["run_id"] == run.id
        assert payload["client_message_id"] == "lineage-1"
        assert payload["task_id"] is None
        assert event.thread_id == thread.id
        assert event.turn_id == turn.id
        assert event.run_id == run.id
