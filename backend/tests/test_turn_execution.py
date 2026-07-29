"""Route-specific execution contracts for one main-Agent conversation Turn."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
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
from app.orchestrator.skills.registry import SkillRegistry, skill_registry
from app.schemas.conversation import (
    CreateConversationTurnRequest,
    TurnExecutionMode,
    TurnRouteDecision,
)
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
async def test_answer_turn_stays_task_free(session, admin, monkeypatch) -> None:
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="answer-1"
    )

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
        )

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    result = await execute_conversation_turn(
        session, admin, turn, run, _request("answer-1")
    )

    assert result.mode is TurnExecutionMode.ANSWER
    assert result.task_id is None
    assert result.status == "completed"
    assert turn.assistant_response
    assert run.status == "completed"
    for model in (BrainTask, StrategyPlan, AgentInvocation, AgentToolCall):
        assert await session.scalar(select(func.count(model.id))) == 0


@pytest.mark.asyncio
async def test_clarify_turn_persists_question_without_task(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="clarify-1"
    )

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.CLARIFY,
            requires_account_context=True,
            requires_operation_task=False,
            missing_field="period",
            clarifying_question="你希望查看最近多少天？",
        )

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    result = await execute_conversation_turn(
        session, admin, turn, run, _request("clarify-1")
    )

    assert result.mode is TurnExecutionMode.CLARIFY
    assert result.task_id is None
    assert result.response == "你希望查看最近多少天？"
    assert turn.assistant_response == result.response
    assert await session.scalar(select(func.count(BrainTask.id))) == 0


@pytest.mark.asyncio
async def test_query_uses_authorized_account_and_records_one_skill_run(
    session, admin, monkeypatch
) -> None:
    account, thread, turn, run = await _turn_context(
        session, admin, key="query-1"
    )
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
                "period": {"days": params["days"], "end": date.today().isoformat()},
                "metrics": {"play": {"value": 42}},
            }

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    monkeypatch.setattr(
        "app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter()
    )
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
    assert await session.scalar(select(func.count(BrainTask.id))) == 0
    assert await session.scalar(select(func.count(StrategyPlan.id))) == 0
    assert await session.scalar(select(func.count(AgentInvocation.id))) == 0


@pytest.mark.asyncio
async def test_completed_query_duplicate_does_not_reclassify_or_reinvoke(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="query-duplicate"
    )
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

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    monkeypatch.setattr(
        "app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter()
    )
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
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="query-failure"
    )
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

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    monkeypatch.setattr(
        "app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter()
    )
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

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    monkeypatch.setattr(
        "app.services.turn_execution.build_runtime_tool_adapter", lambda: Adapter()
    )
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
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="intelligence-down"
    )

    async def classify(*_args, **_kwargs):
        from app.orchestrator.brain_intelligence import IntelligenceUnavailable

        raise IntelligenceUnavailable("raw-provider-failure")

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
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
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="skill-blocked"
    )

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.SKILL,
            skill_code="not_implemented_skill",
        )

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
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
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="explicit-unknown-skill"
    )

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
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="explicit-unpublished"
    )
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

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    monkeypatch.setattr(
        "app.services.turn_execution.runtime_graph.start_routed", should_not_start
    )
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
async def test_formal_task_forces_non_clarify_route_into_task(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="formal-task"
    )
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

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    monkeypatch.setattr(
        "app.services.turn_execution.runtime_graph.start_routed", start_routed
    )
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


@pytest.mark.parametrize(
    "mode",
    [TurnExecutionMode.TASK, TurnExecutionMode.ACTION],
)
@pytest.mark.asyncio
async def test_strategy_task_creates_exactly_one_task_and_uses_routed_runtime(
    session, admin, monkeypatch, mode
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="task-1"
    )
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

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    monkeypatch.setattr(
        "app.services.turn_execution.runtime_graph.start_routed", start_routed
    )
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
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="task-failure"
    )
    starts = 0

    async def classify(*_args, **_kwargs):
        return _decision(TurnExecutionMode.TASK)

    async def start_routed(*_args, **_kwargs):
        nonlocal starts
        starts += 1
        raise RuntimeError("runtime-secret")

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    monkeypatch.setattr(
        "app.services.turn_execution.runtime_graph.start_routed", start_routed
    )
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

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    monkeypatch.setattr(
        "app.services.turn_execution.runtime_graph.start_routed", start_routed
    )
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
    if runtime_state == "failed":
        assert task.status == BrainTaskStatus.FAILED
    else:
        assert task.status == BrainTaskStatus.PENDING_CONFIRMATION


@pytest.mark.asyncio
async def test_task_free_events_have_turn_lineage_and_publish_after_commit(
    session, admin, monkeypatch
) -> None:
    account, thread, turn, run = await _turn_context(
        session, admin, key="lineage-1"
    )
    published: list[int] = []

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.ANSWER,
            requires_account_context=False,
        )

    async def publish(_event_type, _payload, **kwargs):
        event_id = kwargs["event_id"]
        row = await session.get(Event, event_id)
        await session.refresh(turn)
        assert row is not None
        assert turn.assistant_response
        published.append(event_id)

    monkeypatch.setattr(
        "app.services.turn_execution.brain_intelligence.classify_turn", classify
    )
    monkeypatch.setattr(
        "app.orchestrator.brain_runtime.publish_realtime_event", publish
    )
    await execute_conversation_turn(
        session, admin, turn, run, _request("lineage-1")
    )

    events = list(
        await session.scalars(
            select(Event)
            .where(Event.type.like("brain.runtime.%"))
            .order_by(Event.id)
        )
    )
    assert published == [event.id for event in events]
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
