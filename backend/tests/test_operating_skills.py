"""Execution contracts for the first account-operations Skill loop."""

import asyncio
from time import monotonic
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict
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
)
from app.models.enums import (
    AccountStatus,
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    ContentStage,
    ContentStatus,
    DeliverableStatus,
    DeliverableType,
    Platform,
    UserRole,
)
from app.orchestrator.runtime_scope import RuntimeScope
from app.orchestrator.skill_runtime import (
    SkillRecoveryConflict,
    SkillRuntime,
    run_bounded_stage,
)
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.capability_request import CapabilityRequest
from app.services.runtime_deliverables import write_runtime_deliverable
from app.tools import ToolAdapter, ToolExecutionContext, ToolSpec


async def _scope(session, admin, *, key: str, message: str):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname=f"account-{key}",
        status=AccountStatus.ACTIVE,
        auth={"auth_status": "authorized", "data_sync_status": "ready"},
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
        user_input=message,
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
        request_payload={"message": message},
    )
    session.add(run)
    await session.commit()
    return account, thread, turn, run


def _capability_request(
    *,
    admin,
    account,
    thread,
    turn,
    run,
    skill_code: str,
    structured_input: dict,
) -> CapabilityRequest:
    return CapabilityRequest(
        org_id=admin.org_id,
        user_id=admin.id,
        account_id=account.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        message=turn.user_input,
        requested_skill_code=skill_code,
        execution_preference="FORMAL_TASK",
        structured_input=structured_input,
    )


class _Tools:
    def __init__(self) -> None:
        class EmptyParams(BaseModel):
            model_config = ConfigDict(extra="forbid")

        class DaysParams(BaseModel):
            model_config = ConfigDict(extra="forbid")

            days: int = 30

        class PublishParams(BaseModel):
            model_config = ConfigDict(extra="forbid")

            content_item_id: int
            title: str

        async def profile(_params: EmptyParams, context: ToolExecutionContext):
            return {
                "account_id": context.account_id,
                "nickname": "测试账号",
                "platform": "douyin",
            }

        async def data_context(
            params: DaysParams,
            context: ToolExecutionContext,
        ):
            return {
                "account_id": context.account_id,
                "period": {"days": params.days},
                "coverage": {"content_metrics": "available"},
                "metrics": {"play": {"value": 1200}},
                "sources": [{"batch_id": 7}],
            }

        async def prepare_publish_package(
            params: PublishParams,
            context: ToolExecutionContext,
        ):
            return {
                "account_id": context.account_id,
                "content_item_id": params.content_item_id,
                "status": "prepared",
                "publish_package": {"title": params.title},
            }

        self.executor = DurableToolExecutor(
            ToolAdapter(
                [
                    ToolSpec(
                        name="account.profile",
                        handler=profile,
                        params_model=EmptyParams,
                        side_effect_level="read",
                        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
                    ),
                    ToolSpec(
                        name="account.data_context",
                        handler=data_context,
                        params_model=DaysParams,
                        side_effect_level="read",
                        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
                    ),
                    ToolSpec(
                        name="publish_package_prepare",
                        handler=prepare_publish_package,
                        params_model=PublishParams,
                        side_effect_level="idempotent_write",
                        allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
                        execution_phase="prepare",
                    ),
                ]
            )
        )

    async def execute(self, **kwargs):
        return await self.executor.execute(**kwargs)


class _Harness:
    def __init__(self) -> None:
        self.calls: list[AgentCode] = []

    async def execute(self, *args, **kwargs):
        session = args[0]
        code = kwargs["code"]
        self.calls.append(code)
        output = (
            {
                "title": "玻璃贴膜避坑指南",
                "hook": "贴膜前先看这三个坑。",
                "scenes": ["常见误区", "真实案例", "选择建议"],
                "duration_seconds": 60,
                "bgm_suggestion": "轻节奏",
            }
            if code is AgentCode.CONTENT_DIRECTOR
            else {
                "period": "最近30天",
                "summary": "内容已有播放基础，但互动承接不足。",
                "key_metrics": {"play": 1200},
                "highlights": ["案例内容表现较好"],
                "issues": ["评论互动不足"],
                "optimization_suggestions": ["强化结尾提问和私信引导"],
            }
        )
        invocation = AgentInvocation(
            task_id=kwargs["task"].id,
            run_id=kwargs["run_id"],
            skill_run_id=kwargs["skill_run_id"],
            thread_id=kwargs["thread_id"],
            turn_id=kwargs["turn_id"],
            step_key=kwargs["step_key"],
            attempt=kwargs["attempt"],
            agent_code=code,
            agent_name=code.value,
            status=AgentInvocationStatus.DONE,
            output_summary=f"{code.value} completed",
            upstream=[{"trace_only_output": output}],
        )
        session.add(invocation)
        await session.commit()
        await session.refresh(invocation)
        return SimpleNamespace(
            invocation=invocation,
            deliverable=None,
            output=output,
        )


@pytest.mark.asyncio
async def test_bounded_stage_overlaps_isolated_work_and_preserves_definition_order():
    active = 0
    peak = 0
    session_ids: set[int] = set()

    async def execute(index: int) -> str:
        nonlocal active, peak
        session_id = index + 100
        session_ids.add(session_id)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.04 if index == 0 else 0.01)
        active -= 1
        return f"expert-{index}"

    started = monotonic()
    results = await run_bounded_stage(
        [lambda index=index: execute(index) for index in range(4)],
        limit=3,
    )

    assert results == ["expert-0", "expert-1", "expert-2", "expert-3"]
    assert peak == 3
    assert len(session_ids) == 4
    assert monotonic() - started < 0.075


@pytest.mark.asyncio
async def test_bounded_stage_waits_for_all_failures_before_raising():
    completed: list[str] = []

    async def fail() -> str:
        await asyncio.sleep(0.01)
        raise RuntimeError("expert failed")

    async def finish() -> str:
        await asyncio.sleep(0.02)
        completed.append("audited")
        return "done"

    with pytest.raises(RuntimeError, match="expert failed"):
        await run_bounded_stage([fail, finish], limit=3)

    assert completed == ["audited"]


@pytest.mark.parametrize(
    ("skill_code", "message", "artifact_type", "expert_codes", "expected_status"),
    [
        (
            "topic_planning",
            "给我策划未来一周的五个选题",
            "topic_plan",
            [AgentCode.CONTENT_DIRECTOR],
            "completed",
        ),
        (
            "script_generation",
            "写一个玻璃贴膜避坑短视频脚本",
            "video_script",
            [AgentCode.CONTENT_DIRECTOR],
            "completed",
        ),
        (
            "publishing_preparation",
            "给这个内容生成发布前检查清单",
            "publish_calendar",
            [AgentCode.OPERATOR],
            "waiting_permission",
        ),
        (
            "performance_review",
            "复盘最近30天的账号表现",
            "review_report",
            [AgentCode.OPERATOR, AgentCode.CONTENT_DIRECTOR],
            "completed",
        ),
    ],
)
@pytest.mark.asyncio
async def test_operating_skill_executes_bounded_experts_and_persists_artifact(
    session,
    admin,
    skill_code,
    message,
    artifact_type,
    expert_codes,
    expected_status,
) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key=f"skill-{skill_code}",
        message=message,
    )
    harness = _Harness()
    runtime = SkillRuntime(tool_executor=_Tools(), harness=harness)

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code=skill_code,
    )

    assert result.status == expected_status
    assert result.artifact_type == artifact_type
    assert result.artifact_id is not None
    assert result.report["account_id"] == account.id
    assert result.report["participating_experts"] == [code.value for code in expert_codes]
    assert harness.calls == expert_codes
    assert await session.scalar(select(func.count(BrainTask.id))) == 1
    assert await session.scalar(select(func.count(SkillRun.id))) == 1
    assert await session.scalar(select(func.count(Deliverable.id))) == 1


@pytest.mark.asyncio
async def test_generic_operating_skill_persists_real_execution_boundaries(
    session,
    admin,
) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="generic-durable-boundaries",
        message="复盘最近30天的账号表现",
    )

    result = await SkillRuntime(tool_executor=_Tools(), harness=_Harness()).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="performance_review",
    )
    reliable_types = {
        "step.started",
        "step.completed",
        "step.failed",
        "deliverable.updated",
        "turn.completed",
        "turn.failed",
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
        ("step.started", "read_data"),
        ("step.completed", "read_data"),
        ("step.started", "specialist_work"),
        ("step.completed", "specialist_work"),
        ("step.started", "prepare_deliverable"),
        ("deliverable.updated", None),
        ("step.completed", "prepare_deliverable"),
        ("turn.completed", None),
    ]
    assert [event.sequence for event in events] == list(range(1, 9))
    assert {
        (event.org_id, event.account_id, event.thread_id, event.turn_id, event.run_id)
        for event in events
    } == {(admin.org_id, account.id, thread.id, turn.id, run.id)}
    assert {event.skill_run_id for event in events} == {result.skill_run_id}


@pytest.mark.asyncio
async def test_deliverable_and_updated_event_rollback_together(session, admin) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="deliverable-event-rollback",
        message="生成报告",
    )
    content = ContentItem(
        created_by_id=admin.id,
        account_id=account.id,
        title="rollback report",
        current_stage=ContentStage.OPERATION,
        status=ContentStatus.IN_PROGRESS,
    )
    session.add(content)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title="rollback task",
        status=BrainTaskStatus.RUNNING,
        progress=0,
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
        idempotency_key="skill:rollback",
        skill_code="performance_review",
        skill_version=1,
        status="running",
        input_snapshot={},
        output_snapshot={},
    )
    session.add(skill_run)
    await session.commit()
    scope = await RuntimeScope.from_conversation(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
    )
    scope = await scope.bind_task(session, task)
    scope = await scope.bind_skill(session, skill_run)

    deliverable = await write_runtime_deliverable(
        session,
        scope=scope,
        content=content,
        agent_code="06-operator",
        deliverable_type=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"summary": "pending transaction"},
    )
    deliverable_id = deliverable.id
    durable_event = await session.scalar(
        select(Event).where(
            Event.turn_id == turn.id,
            Event.type == "deliverable.updated",
        )
    )
    assert durable_event is not None
    durable_event_id = durable_event.id

    await session.rollback()

    assert await session.get(Deliverable, deliverable_id) is None
    assert await session.get(Event, durable_event_id) is None


@pytest.mark.asyncio
async def test_failed_stage_context_does_not_leak_into_next_execution(
    session,
    admin,
) -> None:
    class PausingTools:
        async def execute(self, **_kwargs):
            return SimpleNamespace(status="failed", result=None, tool_call=None)

    _first_account, first_thread, first_turn, first_run = await _scope(
        session,
        admin,
        key="stage-context-first",
        message="先失败",
    )
    first = await SkillRuntime(
        tool_executor=PausingTools(),
        harness=_Harness(),
    ).execute(
        session,
        user=admin,
        thread=first_thread,
        turn=first_turn,
        run=first_run,
        skill_code="performance_review",
    )
    _second_account, second_thread, second_turn, second_run = await _scope(
        session,
        admin,
        key="stage-context-second",
        message="再成功",
    )
    second = await SkillRuntime(
        tool_executor=_Tools(),
        harness=_Harness(),
    ).execute(
        session,
        user=admin,
        thread=second_thread,
        turn=second_turn,
        run=second_run,
        skill_code="performance_review",
    )
    leaked_failures = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == second_turn.id,
                Event.type == "step.failed",
            )
        )
    )

    assert first.status == "failed"
    assert second.status == "completed"
    assert leaked_failures == []


@pytest.mark.asyncio
async def test_content_publishing_persists_only_its_real_publish_stage(
    session,
    admin,
) -> None:
    from tests.test_content_publishing_skill import _PublishingTools
    from tests.test_visual_brief_skill import _source

    account, thread, turn, run = await _scope(
        session,
        admin,
        key="durable-content-publishing",
        message="发布这个内容",
    )
    source = await _source(
        session,
        account_id=account.id,
        created_by_id=admin.id,
        status=DeliverableStatus.APPROVED,
    )
    result = await SkillRuntime(tool_executor=_PublishingTools()).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="content_publishing",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="content_publishing",
            structured_input={"approved_publish_artifact_id": source.id},
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
                        "turn.completed",
                    }
                ),
            )
            .order_by(Event.sequence)
        )
    )

    assert result.status == "completed"
    assert [(event.type, event.payload.get("step")) for event in events] == [
        ("step.started", "publish_content"),
        ("step.completed", "publish_content"),
        ("turn.completed", None),
    ]


@pytest.mark.asyncio
async def test_operation_iteration_persists_deterministic_plan_boundary(
    session,
    admin,
) -> None:
    from tests.test_operation_iteration_skill import _artifact

    account, thread, turn, run = await _scope(
        session,
        admin,
        key="durable-operation-iteration",
        message="根据复盘安排下周运营",
    )
    review = await _artifact(
        session,
        admin,
        account,
        kind=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.APPROVED,
    )
    result = await SkillRuntime().execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="operation_iteration",
            structured_input={
                "confirmed_review_artifact_id": review.id,
                "cycle_days": 7,
                "script_duration_seconds": 30,
            },
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
                        "turn.completed",
                    }
                ),
            )
            .order_by(Event.sequence)
        )
    )

    assert result.status == "completed"
    script_node = next(
        node
        for node in result.report["child_skill_graph"]
        if node["skill_code"] == "script_generation"
    )
    assert script_node["input"] == {"duration_seconds": 30}
    assert [(event.type, event.payload.get("step")) for event in events] == [
        ("step.started", "prepare_deliverable"),
        ("deliverable.updated", None),
        ("step.completed", "prepare_deliverable"),
        ("turn.completed", None),
    ]


@pytest.mark.asyncio
async def test_topic_skill_honors_structured_days_and_topic_count(session, admin) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="typed-topic-input",
        message="规划未来14天的10个选题",
    )
    runtime = SkillRuntime(tool_executor=_Tools(), harness=_Harness())

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="topic_planning",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="topic_planning",
            structured_input={"days": 14, "topic_count": 10},
        ),
    )

    skill_run = await session.scalar(select(SkillRun).where(SkillRun.run_id == run.id))
    assert skill_run is not None
    assert skill_run.input_snapshot == {
        "account_id": account.id,
        "days": 14,
        "topic_count": 10,
    }
    assert result.report["period"] == "未来 14 天"
    assert len(result.report["topics"]) == 10


@pytest.mark.asyncio
async def test_script_skill_prefers_explicit_duration_over_expert_default(session, admin) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="typed-script-input",
        message="生成一个30秒脚本",
    )
    runtime = SkillRuntime(tool_executor=_Tools(), harness=_Harness())

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="script_generation",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="script_generation",
            structured_input={
                "duration_seconds": 30,
                "presentation_format": "product_video",
            },
        ),
    )

    skill_run = await session.scalar(select(SkillRun).where(SkillRun.run_id == run.id))
    assert skill_run is not None
    assert skill_run.input_snapshot == {
        "account_id": account.id,
        "duration_seconds": 30,
        "presentation_format": "product_video",
    }
    assert result.report["duration_seconds"] == 30
    assert result.report["presentation_format"] == "product_video"


@pytest.mark.asyncio
async def test_skill_recovery_rejects_changed_structured_input(session, admin) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="typed-recovery-conflict",
        message="规划未来14天的10个选题",
    )
    runtime = SkillRuntime(tool_executor=_Tools(), harness=_Harness())
    original_request = _capability_request(
        admin=admin,
        account=account,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="topic_planning",
        structured_input={"days": 14, "topic_count": 10},
    )
    await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="topic_planning",
        capability_request=original_request,
    )

    changed_request = original_request.model_copy(
        update={"structured_input": {"days": 7, "topic_count": 10}}
    )
    with pytest.raises(SkillRecoveryConflict, match="SKILL_RECOVERY_INPUT_CONFLICT"):
        await runtime.execute(
            session,
            user=admin,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="topic_planning",
            capability_request=changed_request,
        )


@pytest.mark.asyncio
async def test_publishing_preparation_executes_declared_prepare_tool(session, admin) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key="publishing-tool-plan",
        message="为这条内容生成发布准备包",
    )
    runtime = SkillRuntime(tool_executor=_Tools(), harness=_Harness())

    result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="publishing_preparation",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="publishing_preparation",
            structured_input={},
        ),
    )

    calls = list(
        await session.scalars(
            select(AgentToolCall).where(AgentToolCall.skill_run_id == result.skill_run_id)
        )
    )
    assert result.status == "waiting_permission"
    assert [(call.tool_code, call.status) for call in calls] == [
        ("publish_package_prepare", "waiting_approval")
    ]
    assert calls[0].requires_human_confirmation is True
    assert calls[0].permission_mode == "confirm"
    skill_run = await session.get(SkillRun, result.skill_run_id)
    assert skill_run is not None
    assert skill_run.status == "waiting_permission"
    await session.refresh(run)
    await session.refresh(turn)
    assert run.status == "waiting_permission"
    assert turn.status == "waiting_permission"
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
        == 0
    )
    paused_events = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == turn.id,
                Event.type == "turn.paused",
            )
        )
    )
    assert len(paused_events) == 1
    assert paused_events[0].payload == {
        "status": "waiting_permission",
        "message": paused_events[0].payload["message"],
    }


@pytest.mark.parametrize(
    ("approved", "expected_runtime_status", "expected_deliverable_status"),
    [
        (True, "completed", DeliverableStatus.APPROVED),
        (False, "blocked", DeliverableStatus.REJECTED),
    ],
)
@pytest.mark.asyncio
async def test_publishing_finish_approval_converges_typed_skill_without_legacy_resume(
    client,
    session,
    admin,
    approved,
    expected_runtime_status,
    expected_deliverable_status,
) -> None:
    account, thread, turn, run = await _scope(
        session,
        admin,
        key=f"publishing-finish-approval-{approved}",
        message="为这条内容生成发布准备包",
    )
    result = await SkillRuntime(tool_executor=_Tools(), harness=_Harness()).execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=run,
        skill_code="publishing_preparation",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=run,
            skill_code="publishing_preparation",
            structured_input={},
        ),
    )
    call = await session.scalar(
        select(AgentToolCall).where(AgentToolCall.skill_run_id == result.skill_run_id)
    )
    assert call is not None
    login = await client.post(
        "/auth/login",
        json={"email": "admin@test.com", "password": "admin-pw-123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.post(
        f"/brain/tool-calls/{call.id}/approve",
        headers=headers,
        json={"approved": approved, "comment": "人工审批结论"},
    )

    assert response.status_code == 200
    await session.refresh(run)
    await session.refresh(turn)
    skill_run = await session.get(SkillRun, result.skill_run_id)
    deliverable = await session.get(Deliverable, result.artifact_id)
    assert skill_run is not None
    assert deliverable is not None
    assert run.status == expected_runtime_status
    assert turn.status == expected_runtime_status
    assert skill_run.status == expected_runtime_status
    assert deliverable.status is expected_deliverable_status
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
    assert [event.type for event in terminal_events] == [
        "turn.completed" if approved else "turn.blocked"
    ]
