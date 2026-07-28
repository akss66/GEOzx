from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.events import record_runtime_event_once
from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    Event,
    OrchestrationPlan,
    TaskBrief,
)
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    BrainTaskType,
    Platform,
)
from app.orchestrator import brain_runtime as brain_runtime_module
from app.orchestrator.brain_runtime import BrainRuntimeGraph
from app.orchestrator.runtime_budget import RuntimeBudgetGuard, RuntimeBudgetLimits
from app.schemas.brain import RuntimeNextStep, RuntimeToolCall


def _state(**overrides):
    state = {
        "round_index": 1,
        "runtime_started_at": datetime.now(UTC).isoformat(),
        "expert_dispatch_history": [],
        "tool_call_count": 0,
        "token_count": 0,
        "cost_usd": 0.0,
    }
    state.update(overrides)
    return state


async def _runtime_retry_fixture(session, admin, *, client_message_id: str):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=f"Runtime account {client_message_id}",
        auth={"auth_status": "authorized"},
    )
    session.add(account)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title=f"Runtime task {client_message_id}",
        type=BrainTaskType.REVIEW_OPTIMIZATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
        thread_id=f"runtime-thread-{client_message_id}",
    )
    task.brief = TaskBrief(
        goal="Resume runtime safely",
        project_id=None,
        project_name=None,
        platforms=[Platform.DOUYIN.value],
        account_ids=[account.id],
        cycle="current",
        content_goal="resume",
        risk_constraints=[],
        expected_outputs=[],
        confirmation_actions=[],
    )
    task.plan = OrchestrationPlan(
        summary="resume",
        steps=[],
        quality_gates=[],
        estimated_cost=Decimal("0"),
        requires_human_confirmation=False,
    )
    session.add(task)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        client_message_id=client_message_id,
        request_payload={},
        result_payload={},
    )
    session.add(run)
    await session.commit()
    await session.refresh(task)
    await session.refresh(task, attribute_names=["brief", "plan"])
    await session.refresh(run)
    return account, task, run


def test_same_expert_requires_new_purpose_or_evidence_before_repeat():
    guard = RuntimeBudgetGuard(RuntimeBudgetLimits(max_expert_calls_per_code=3))
    state = _state()

    first = guard.authorize_experts(
        state,
        ["01-positioning"],
        purpose="核对账号定位",
        evidence_refs=["account-profile:1"],
    )
    repeated = guard.authorize_experts(
        first.state,
        ["01-positioning"],
        purpose="核对账号定位",
        evidence_refs=["account-profile:1"],
    )
    revised = guard.authorize_experts(
        first.state,
        ["01-positioning"],
        purpose="根据新作品数据修正定位",
        evidence_refs=["metrics-snapshot:2"],
    )

    assert first.allowed_codes == ["01-positioning"]
    assert repeated.allowed_codes == []
    assert repeated.blocked_reason == "duplicate_expert_dispatch"
    assert revised.allowed_codes == ["01-positioning"]
    assert len(revised.state["expert_dispatch_history"]) == 2


def test_expert_and_tool_budgets_are_enforced_before_dispatch():
    guard = RuntimeBudgetGuard(
        RuntimeBudgetLimits(
            max_expert_calls=2,
            max_expert_calls_per_code=1,
            max_tool_calls=2,
        )
    )
    first = guard.authorize_experts(
        _state(),
        ["01-positioning", "02-content-director"],
        purpose="形成定位和内容方向",
        evidence_refs=[],
    )
    blocked_expert = guard.authorize_experts(
        first.state,
        ["03-art-director"],
        purpose="继续生成视觉方向",
        evidence_refs=[],
    )
    tools = guard.authorize_tools(first.state, 2)
    blocked_tools = guard.authorize_tools(tools.state, 1)

    assert blocked_expert.allowed_codes == []
    assert blocked_expert.blocked_reason == "expert_call_budget_exhausted"
    assert tools.allowed_count == 2
    assert blocked_tools.allowed_count == 0
    assert blocked_tools.blocked_reason == "tool_call_budget_exhausted"


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (_state(round_index=9), "round_budget_exhausted"),
        (_state(token_count=100_001), "token_budget_exhausted"),
        (_state(cost_usd=5.01), "cost_budget_exhausted"),
        (
            _state(
                runtime_started_at=(datetime.now(UTC) - timedelta(seconds=901)).isoformat()
            ),
            "elapsed_time_budget_exhausted",
        ),
    ],
)
def test_runtime_budget_reports_terminal_reason(state, reason):
    guard = RuntimeBudgetGuard(
        RuntimeBudgetLimits(
            max_rounds=8,
            max_tokens=100_000,
            max_cost_usd=5,
            max_elapsed_seconds=900,
        )
    )

    assert guard.exhaustion_reason(state) == reason


def test_request_permission_requires_a_concrete_tool_call():
    with pytest.raises(ValidationError):
        RuntimeNextStep(
            action="request_permission",
            rationale="需要执行受控动作",
            handoff_message="需要你确认后继续。",
        )

    step = RuntimeNextStep(
        action="request_permission",
        rationale="需要执行受控动作",
        handoff_message="需要你确认后继续。",
        tool_calls=[
            RuntimeToolCall(
                tool_code="publish.prepare",
                arguments={},
                purpose="生成发布包",
                idempotency_key="publish-package-1",
            )
        ],
    )

    assert step.action == "request_permission"


@pytest.mark.asyncio
async def test_runtime_event_idempotency_reuses_persisted_event_and_isolates_account(
    session, admin
) -> None:
    """The DB unique key, rather than a process-local check, owns runtime replay."""

    identity = {
        "org_id": admin.org_id,
        "account_id": 101,
        "run_id": 202,
        "client_message_id": "retry-message-1",
        "event_type": "brain.runtime.message_done",
        "semantic_key": "main-agent:done",
    }
    first, created_first = await record_runtime_event_once(
        session,
        payload={"content": "persisted reply"},
        **identity,
    )
    second, created_second = await record_runtime_event_once(
        session,
        payload={"content": "retry must reuse this"},
        **identity,
    )
    other_account, created_other_account = await record_runtime_event_once(
        session,
        payload={"content": "different account is independent"},
        **{**identity, "account_id": 102},
    )
    other_run, created_other_run = await record_runtime_event_once(
        session,
        payload={"content": "different run is independent"},
        **{**identity, "run_id": 203},
    )
    other_client_message, created_other_client_message = await record_runtime_event_once(
        session,
        payload={"content": "different user message is independent"},
        **{**identity, "client_message_id": "retry-message-2"},
    )
    await session.commit()

    rows = list(
        await session.scalars(
            select(Event).where(Event.type == "brain.runtime.message_done")
        )
    )
    assert created_first is True
    assert created_second is False
    assert created_other_account is True
    assert created_other_run is True
    assert created_other_client_message is True
    assert first.id == second.id
    assert first.payload["content"] == "persisted reply"
    assert other_account.id != first.id
    assert other_run.id != first.id
    assert other_client_message.id != first.id
    assert len(rows) == 4
    assert first.payload["org_id"] == admin.org_id
    assert first.payload["account_id"] == 101
    assert first.payload["run_id"] == 202
    assert first.payload["client_message_id"] == "retry-message-1"


@pytest.mark.asyncio
async def test_resume_after_permission_reuses_runtime_event_identity_on_retry(
    session, admin, monkeypatch
) -> None:
    account, task, run = await _runtime_retry_fixture(
        session, admin, client_message_id="permission-retry-1"
    )
    tool_call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        tool_code="account.profile",
        tool_name="Account profile",
        status="success",
        requires_human_confirmation=True,
        input_summary="{}",
        output_summary="approved",
        meta={"result": {"ok": True}},
    )
    session.add(tool_call)
    await session.commit()

    runtime = BrainRuntimeGraph()
    runtime._native_interrupts = False

    async def fake_resume(*_args, **_kwargs):
        await runtime._record_event(
            session,
            task,
            "brain.runtime.completed",
            {
                "message": "resume finished",
                "semantic_key": "resume-permission-finished",
            },
        )

    monkeypatch.setattr(runtime._resume_graph, "ainvoke", fake_resume)

    await runtime.resume_after_permission(
        session,
        task,
        tool_call,
        True,
        agent_run_id=run.id,
        agent_run_attempt=1,
    )
    await runtime.resume_after_permission(
        session,
        task,
        tool_call,
        True,
        agent_run_id=run.id,
        agent_run_attempt=2,
    )

    events = [
        event
        for event in (
            await session.scalars(
                select(Event)
                .where(
                    Event.type.in_(
                        ["brain.runtime.resumed", "brain.runtime.completed"]
                    )
                )
                .order_by(Event.id)
            )
        ).all()
        if (event.payload or {}).get("task_id") == task.id
    ]
    assert [event.type for event in events] == [
        "brain.runtime.resumed",
        "brain.runtime.completed",
    ]
    assert all(event.idempotency_key is not None for event in events)
    assert all(event.payload["run_id"] == run.id for event in events)
    assert all(event.payload["account_id"] == account.id for event in events)


@pytest.mark.asyncio
async def test_resume_after_decision_reuses_runtime_event_identity_on_retry(
    session, admin, monkeypatch
) -> None:
    account, task, run = await _runtime_retry_fixture(
        session, admin, client_message_id="decision-retry-1"
    )
    runtime = BrainRuntimeGraph()
    runtime._native_interrupts = False

    async def fake_resume(*_args, **_kwargs):
        await runtime._record_event(
            session,
            task,
            "brain.runtime.completed",
            {
                "message": "decision resume finished",
                "semantic_key": "resume-decision-finished",
            },
        )

    monkeypatch.setattr(runtime._smart_resume_graph, "ainvoke", fake_resume)

    await runtime.resume_after_decision(
        session,
        task,
        decision_id="decision-1",
        choice_id="choice-a",
        choice_title="Pick A",
        record_selection=False,
        agent_run_id=run.id,
        agent_run_attempt=1,
    )
    await runtime.resume_after_decision(
        session,
        task,
        decision_id="decision-1",
        choice_id="choice-a",
        choice_title="Pick A",
        record_selection=False,
        agent_run_id=run.id,
        agent_run_attempt=2,
    )

    events = [
        event
        for event in (
            await session.scalars(
                select(Event)
                .where(Event.type == "brain.runtime.completed")
                .order_by(Event.id)
            )
        ).all()
        if (event.payload or {}).get("task_id") == task.id
    ]
    assert len(events) == 1
    assert events[0].payload["run_id"] == run.id
    assert events[0].payload["account_id"] == account.id
    assert events[0].payload["semantic_key"] == "resume-decision-finished"


@pytest.mark.asyncio
async def test_stream_main_agent_turn_skips_model_call_when_ack_is_already_persisted(
    session, admin, monkeypatch
) -> None:
    account, task, run = await _runtime_retry_fixture(
        session, admin, client_message_id="ack-retry-1"
    )
    runtime = BrainRuntimeGraph()
    await record_runtime_event_once(
        session,
        org_id=task.org_id,
        account_id=account.id,
        run_id=run.id,
        client_message_id=run.client_message_id,
        event_type="brain.runtime.message_done",
        semantic_key="00-decision:main-agent.acknowledgement",
        payload={
            "task_id": task.id,
            "semantic_key": "00-decision:main-agent.acknowledgement",
            "message": "persisted acknowledgement",
        },
    )

    async def should_not_call(*_args, **_kwargs):
        raise AssertionError("acknowledgement should reuse the persisted message")

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime._chat_main_agent",
        should_not_call,
    )

    token = brain_runtime_module._runtime_event_identity.set(
        (task.org_id, account.id, run.id, run.client_message_id)
    )
    try:
        await runtime._stream_main_agent_turn(session, task)
    finally:
        brain_runtime_module._runtime_event_identity.reset(token)


@pytest.mark.asyncio
async def test_stream_summary_turn_skips_model_call_when_summary_is_already_persisted(
    session, admin, monkeypatch
) -> None:
    account, task, run = await _runtime_retry_fixture(
        session, admin, client_message_id="summary-retry-1"
    )
    runtime = BrainRuntimeGraph()
    await record_runtime_event_once(
        session,
        org_id=task.org_id,
        account_id=account.id,
        run_id=run.id,
        client_message_id=run.client_message_id,
        event_type="brain.runtime.message_done",
        semantic_key="00-decision:main-agent.summary",
        payload={
            "task_id": task.id,
            "semantic_key": "00-decision:main-agent.summary",
            "message": "persisted summary",
        },
    )

    async def should_not_call(*_args, **_kwargs):
        raise AssertionError("summary should reuse the persisted message")

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime._chat_main_agent",
        should_not_call,
    )

    token = brain_runtime_module._runtime_event_identity.set(
        (task.org_id, account.id, run.id, run.client_message_id)
    )
    try:
        await runtime._stream_summary_turn(
            session,
            task,
            observations=[{"kind": "expert_result", "summary": "ready"}],
        )
    finally:
        brain_runtime_module._runtime_event_identity.reset(token)


@pytest.mark.asyncio
async def test_main_agent_acknowledgement_is_bounded_and_never_calls_the_model(
    session, admin, monkeypatch
) -> None:
    account, task, run = await _runtime_retry_fixture(
        session, admin, client_message_id="authority-ack-1"
    )
    runtime = BrainRuntimeGraph()

    async def should_not_call(*_args, **_kwargs):
        raise AssertionError("pre-execution acknowledgement must not call the model")

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime._chat_main_agent",
        should_not_call,
    )
    token = brain_runtime_module._runtime_event_identity.set(
        (task.org_id, account.id, run.id, run.client_message_id)
    )
    try:
        await runtime._stream_main_agent_turn(session, task)
    finally:
        brain_runtime_module._runtime_event_identity.reset(token)

    acknowledgement = await session.scalar(
        select(Event).where(
            Event.type == "brain.runtime.message_done",
            Event.payload["semantic_key"].as_string()
            == "00-decision:main-agent.acknowledgement",
        )
    )
    assert acknowledgement is not None
    content = acknowledgement.payload["content"]
    assert content == (
        "已收到你的账号运营需求。我会先核对数据和执行条件；"
        "只有对应专家实际完成分析后，才会向你交付正式结论。"
    )
    assert "完播率下降" not in content
    assert "账号定位存在问题" not in content
    assert "建议优化" not in content


@pytest.mark.asyncio
async def test_summary_is_blocked_without_a_completed_specialist_invocation(
    session, admin, monkeypatch
) -> None:
    account, task, run = await _runtime_retry_fixture(
        session, admin, client_message_id="authority-blocked-1"
    )
    runtime = BrainRuntimeGraph()

    async def should_not_call(*_args, **_kwargs):
        raise AssertionError("main Agent must not replace a missing specialist")

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime._chat_main_agent",
        should_not_call,
    )
    token = brain_runtime_module._runtime_event_identity.set(
        (task.org_id, account.id, run.id, run.client_message_id)
    )
    try:
        delivered = await runtime._stream_summary_turn(
            session,
            task,
            observations=[
                {
                    "invocation_id": 999999,
                    "agent_code": AgentCode.POSITIONING.value,
                    "summary": "untrusted diagnosis",
                }
            ],
        )
    finally:
        brain_runtime_module._runtime_event_identity.reset(token)

    assert delivered is False
    blocked = await session.scalar(
        select(Event).where(Event.type == "brain.runtime.summary_blocked")
    )
    assert blocked is not None
    assert blocked.payload["reason"] == "no_completed_specialist_invocation"
    assert "不能生成正式诊断结论" in blocked.payload["message"]
    assert "untrusted diagnosis" not in blocked.payload["message"]


@pytest.mark.asyncio
async def test_summary_uses_only_a_completed_specialist_invocation(
    session, admin, monkeypatch
) -> None:
    account, task, run = await _runtime_retry_fixture(
        session, admin, client_message_id="authority-completed-1"
    )
    invocation = AgentInvocation(
        task_id=task.id,
        run_id=run.id,
        step_key="round-1:01-positioning",
        attempt=1,
        agent_code=AgentCode.POSITIONING,
        agent_name="账号定位专家",
        status=AgentInvocationStatus.DONE,
        input_summary="diagnose positioning",
        output_summary="定位证据完整，建议聚焦家庭装修用户。",
        model="test-specialist",
        token_count=10,
        cost=Decimal("0"),
    )
    session.add(invocation)
    await session.commit()
    await session.refresh(task, attribute_names=["brief", "plan"])
    captured_messages: list[dict] = []

    async def capture_summary(
        _session,
        _task,
        prompt_id,
        _operating_context,
        messages,
    ):
        assert prompt_id == "main-agent.summary"
        captured_messages.extend(messages)
        return None

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime._chat_main_agent",
        capture_summary,
    )
    runtime = BrainRuntimeGraph()
    token = brain_runtime_module._runtime_event_identity.set(
        (task.org_id, account.id, run.id, run.client_message_id)
    )
    try:
        delivered = await runtime._stream_summary_turn(
            session,
            task,
            observations=[
                {
                    "invocation_id": invocation.id,
                    "agent_code": AgentCode.POSITIONING.value,
                    "summary": invocation.output_summary,
                },
                {
                    "invocation_id": 999999,
                    "agent_code": AgentCode.OPERATOR.value,
                    "summary": "must not leak",
                },
            ],
            required_expert_codes=[AgentCode.POSITIONING.value],
        )
    finally:
        brain_runtime_module._runtime_event_identity.reset(token)

    assert delivered is True
    summary_input = captured_messages[-1]["content"]
    assert str(invocation.id) in summary_input
    assert invocation.output_summary in summary_input
    assert "must not leak" not in summary_input

    token = brain_runtime_module._runtime_event_identity.set(
        (task.org_id, account.id, run.id, run.client_message_id)
    )
    try:
        partial_delivered = await runtime._stream_summary_turn(
            session,
            task,
            observations=[
                {
                    "invocation_id": invocation.id,
                    "agent_code": AgentCode.POSITIONING.value,
                    "summary": invocation.output_summary,
                }
            ],
            required_expert_codes=[
                AgentCode.POSITIONING.value,
                AgentCode.OPERATOR.value,
            ],
        )
    finally:
        brain_runtime_module._runtime_event_identity.reset(token)

    assert partial_delivered is False
    blocked = await session.scalar(
        select(Event)
        .where(Event.type == "brain.runtime.summary_blocked")
        .order_by(Event.id.desc())
    )
    assert blocked is not None
    assert blocked.payload["missing_expert_codes"] == [AgentCode.OPERATOR.value]


@pytest.mark.asyncio
async def test_tool_only_result_can_be_reported_without_a_specialist_conclusion(
    session, admin, monkeypatch
) -> None:
    account, task, run = await _runtime_retry_fixture(
        session, admin, client_message_id="authority-tool-only-1"
    )
    captured_messages: list[dict] = []

    async def capture_summary(
        _session,
        _task,
        prompt_id,
        _operating_context,
        messages,
    ):
        assert prompt_id == "main-agent.summary"
        captured_messages.extend(messages)
        return None

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime._chat_main_agent",
        capture_summary,
    )
    runtime = BrainRuntimeGraph()
    token = brain_runtime_module._runtime_event_identity.set(
        (task.org_id, account.id, run.id, run.client_message_id)
    )
    try:
        delivered = await runtime._stream_summary_turn(
            session,
            task,
            observations=[
                {
                    "kind": "tool_result",
                    "tool_code": "account.profile",
                    "summary": "账号授权状态正常。",
                    "result": {"authorized": True},
                }
            ],
        )
    finally:
        brain_runtime_module._runtime_event_identity.reset(token)

    assert delivered is True
    assert "账号授权状态正常" in captured_messages[-1]["content"]


@pytest.mark.asyncio
async def test_legacy_pipeline_projects_only_new_completed_invocations(
    session, admin
) -> None:
    account, task, run = await _runtime_retry_fixture(
        session, admin, client_message_id="legacy-lifecycle-1"
    )
    old_invocation = AgentInvocation(
        task_id=task.id,
        run_id=run.id,
        step_key="old:01-positioning",
        attempt=1,
        agent_code=AgentCode.POSITIONING,
        agent_name="旧账号定位专家",
        status=AgentInvocationStatus.DONE,
        input_summary="old",
        output_summary="old result",
        model="test",
        token_count=1,
        cost=Decimal("0"),
    )
    new_invocation = AgentInvocation(
        task_id=task.id,
        run_id=run.id,
        step_key="new:02-content-director",
        attempt=1,
        agent_code=AgentCode.CONTENT_DIRECTOR,
        agent_name="新内容专家",
        status=AgentInvocationStatus.DONE,
        input_summary="new",
        output_summary="new result",
        model="test",
        token_count=1,
        cost=Decimal("0"),
    )
    failed_invocation = AgentInvocation(
        task_id=task.id,
        run_id=run.id,
        step_key="new:06-operator",
        attempt=1,
        agent_code=AgentCode.OPERATOR,
        agent_name="失败运营专家",
        status=AgentInvocationStatus.FAILED,
        input_summary="failed",
        output_summary="",
        model="test",
        token_count=1,
        cost=Decimal("0"),
    )
    session.add_all([old_invocation, new_invocation, failed_invocation])
    await session.commit()
    await session.refresh(task, attribute_names=["brief", "plan"])

    runtime = BrainRuntimeGraph()
    token = brain_runtime_module._runtime_event_identity.set(
        (task.org_id, account.id, run.id, run.client_message_id)
    )
    try:
        await runtime._record_subagent_results(
            session,
            task,
            exclude_invocation_ids={old_invocation.id},
        )
    finally:
        brain_runtime_module._runtime_event_identity.reset(token)

    lifecycle = (
        await session.scalars(
            select(Event)
            .where(Event.type.like("brain.runtime.subagent_%"))
            .order_by(Event.id)
        )
    ).all()
    assert [event.type for event in lifecycle] == [
        "brain.runtime.subagent_completed"
    ]
    assert lifecycle[0].payload["invocation_id"] == new_invocation.id
