"""Route-specific execution contracts for one main-Agent conversation Turn."""

from __future__ import annotations

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
    Event,
    SkillRun,
    StrategyPlan,
)
from app.models.enums import AccountStatus, BrainTaskStatus, Platform
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


def _request(key: str, message: str | None = None) -> CreateConversationTurnRequest:
    return CreateConversationTurnRequest(
        client_message_id=key,
        message=message or f"message-{key}",
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
async def test_unavailable_skill_is_structured_blocked_without_artifact(
    session, admin, monkeypatch
) -> None:
    _account, _thread, turn, run = await _turn_context(
        session, admin, key="skill-blocked"
    )

    async def classify(*_args, **_kwargs):
        return _decision(
            TurnExecutionMode.SKILL,
            skill_code="account_inspection",
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
    assert result.error_code == "SKILL_EXECUTOR_UNAVAILABLE"
    assert result.task_id is None
    assert run.status == "blocked"
    assert run.error_code == "SKILL_EXECUTOR_UNAVAILABLE"
    assert await session.scalar(select(func.count(ContentItem.id))) == 0
    assert await session.scalar(select(func.count(BrainTask.id))) == 0


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
