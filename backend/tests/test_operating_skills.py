"""Execution contracts for the first account-operations Skill loop."""

import asyncio
from copy import deepcopy
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

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
from app.schemas.brain import RuntimeToolCall
from app.schemas.capability_request import CapabilityRequest
from app.services.agent_runs import acquire_agent_run
from app.services.artifacts import accept_artifact
from app.services.runtime_deliverables import write_runtime_deliverable
from app.services.skill_approvals import finalize_skill_finish_approval
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
        self.calls: list[RuntimeToolCall] = []
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
        self.calls.append(kwargs["request"])
        return await self.executor.execute(**kwargs)


class _Harness:
    def __init__(self) -> None:
        self.calls: list[AgentCode] = []
        self.upstreams: list[dict] = []

    async def execute(self, *args, **kwargs):
        session = args[0]
        code = kwargs["code"]
        self.calls.append(code)
        self.upstreams.append(dict(kwargs["upstream"]))
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
async def test_operation_iteration_real_runtime_child_lifecycle_retry_gate(
    session,
    admin,
    monkeypatch,
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
    tools = _Tools()
    harness = _Harness()

    class ObservedRuntime(SkillRuntime):
        def __init__(self) -> None:
            super().__init__(
                tool_executor=tools,
                harness=harness,
            )
            self.child_requests: list[CapabilityRequest] = []

        async def _execute_child_skill(self, *args, capability_request, **kwargs):
            parent_stage_started = await args[0].scalar(
                select(Event.id).where(
                    Event.turn_id == turn.id,
                    Event.type == "step.started",
                    Event.payload["step"].as_string() == "prepare_deliverable",
                )
            )
            assert parent_stage_started is not None
            self.child_requests.append(capability_request)
            return await super()._execute_child_skill(
                *args,
                capability_request=capability_request,
                **kwargs,
            )

    runtime = ObservedRuntime()

    async def external_counts() -> dict[str, int]:
        return {
            "provider": len(harness.calls),
            "tool": len(tools.calls),
            "expert": int(
                await session.scalar(
                    select(func.count(AgentInvocation.id)).where(
                        AgentInvocation.run_id == run.id
                    )
                )
                or 0
            ),
            "durable_tool": int(
                await session.scalar(
                    select(func.count(AgentToolCall.id)).where(
                        AgentToolCall.skill_run_id.in_(
                            select(SkillRun.id).where(SkillRun.run_id == run.id)
                        )
                    )
                )
                or 0
            ),
        }

    result = await runtime.execute(
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
                "topic_count": 4,
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

    assert result.status == "waiting_user"
    nodes = {node["skill_code"]: node for node in result.report["child_skill_graph"]}
    script_node = nodes["script_generation"]
    topic_node = nodes["topic_planning"]
    assert script_node["input"] == {"duration_seconds": 30}
    assert topic_node["input"] == {"days": 7, "topic_count": 4}
    assert topic_node["status"] == script_node["status"] == "completed"
    assert isinstance(topic_node["artifact_id"], int)
    assert isinstance(script_node["artifact_id"], int)
    requests = {
        request.requested_skill_code: request.structured_input
        for request in runtime.child_requests
    }
    assert requests == {
        "topic_planning": {"days": 7, "topic_count": 4},
        "script_generation": {"duration_seconds": 30},
    }
    child_runs = {
        row.skill_code: row
        for row in await session.scalars(
            select(SkillRun).where(
                SkillRun.run_id == run.id,
                SkillRun.skill_code.in_({"topic_planning", "script_generation"}),
            )
        )
    }
    assert child_runs["topic_planning"].input_snapshot == {
        "account_id": account.id,
        "days": 7,
        "topic_count": 4,
    }
    assert child_runs["script_generation"].input_snapshot == {
        "account_id": account.id,
        "duration_seconds": 30,
        "presentation_format": "storyboard",
    }
    assert all(row.status == "completed" for row in child_runs.values())
    data_context = next(
        call for call in tools.calls if call.tool_code == "account.data_context"
    )
    assert data_context.arguments["days"] == 7
    assert [upstream["structured_input"] for upstream in harness.upstreams] == [
        {"account_id": account.id, "days": 7, "topic_count": 4},
        {
            "account_id": account.id,
            "duration_seconds": 30,
            "presentation_format": "storyboard",
        },
    ]
    visual_node = nodes["visual_brief_generation"]
    assert visual_node["status"] == "waiting_user"
    assert visual_node["error_code"] == "DEPENDENCY_ARTIFACT_APPROVAL_REQUIRED"
    assert nodes["content_calendar_planning"]["status"] == "pending"
    assert nodes["publishing_preparation"]["status"] == "pending"
    assert result.report["required_children_completed"] is False
    assert result.report["interrupt"] == {
        "kind": "artifact_approval_required",
        "skill_code": "visual_brief_generation",
        "source_artifact_ids": [script_node["artifact_id"]],
    }
    await session.refresh(run)
    parent_run = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.skill_code == "operation_iteration",
        )
    )
    task = await session.get(BrainTask, result.task_id)
    assert run.status == "waiting_user"
    assert parent_run is not None and parent_run.status == "waiting_permission"
    assert task is not None and task.progress < 100
    assert sum(event.type == "turn.completed" for event in events) == 0
    assert await external_counts() == {
        "provider": 2,
        "tool": 3,
        "expert": 2,
        "durable_tool": 3,
    }

    calls_before_retry = (len(tools.calls), len(harness.calls))
    replay = await runtime.execute(
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
                "topic_count": 4,
                "script_duration_seconds": 30,
            },
        ),
    )
    assert replay.skill_run_id == result.skill_run_id
    assert (len(tools.calls), len(harness.calls)) == calls_before_retry
    assert await external_counts() == {
        "provider": 2,
        "tool": 3,
        "expert": 2,
        "durable_tool": 3,
    }

    # Simulate a crash after child commits but before the parent snapshot commits.
    stale_parent_output = deepcopy(parent_run.output_snapshot)
    stale_graph = stale_parent_output["report"]["child_skill_graph"]
    for stale_node in stale_graph[:2]:
        stale_node["status"] = "pending"
        stale_node["artifact_id"] = None
    parent_run.output_snapshot = stale_parent_output
    await session.commit()

    await accept_artifact(session, admin, artifact_id=script_node["artifact_id"])
    await session.refresh(run)
    await session.refresh(parent_run)
    assert run.status == "queued"
    assert parent_run.status == "running"
    claimed = await acquire_agent_run(
        session,
        run.id,
        worker_id="operation-test-worker",
        lease_seconds=60,
    )
    assert claimed is not None
    resumed = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=claimed,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=claimed,
            skill_code="operation_iteration",
            structured_input={
                "confirmed_review_artifact_id": review.id,
                "cycle_days": 7,
                "topic_count": 4,
                "script_duration_seconds": 30,
            },
        ),
        lease_owner="operation-test-worker",
        resume_skill_run=parent_run,
    )
    resumed_nodes = {
        node["skill_code"]: node for node in resumed.report["child_skill_graph"]
    }
    visual_node = resumed_nodes["visual_brief_generation"]
    assert resumed.status == "waiting_user"
    assert visual_node["status"] == "needs_review"
    assert visual_node["input"] == {}
    assert resumed_nodes["content_calendar_planning"]["status"] == "pending"
    assert resumed.report["interrupt"]["kind"] == "child_skill_paused"
    assert resumed.report["interrupt"]["skill_code"] == "visual_brief_generation"
    assert isinstance(resumed.report["interrupt"]["child_skill_run_id"], int)
    assert resumed.report["interrupt"]["source_artifact_ids"] == [
        visual_node["artifact_id"]
    ]
    assert runtime.child_requests[-1].requested_skill_code == "visual_brief_generation"
    assert runtime.child_requests[-1].structured_input == {
        "source_artifact_ids": [script_node["artifact_id"]]
    }
    child_ids_after_resume = {
        row.skill_code: row.id
        for row in await session.scalars(
            select(SkillRun).where(SkillRun.run_id == run.id)
        )
    }
    assert child_ids_after_resume["topic_planning"] == child_runs["topic_planning"].id
    assert child_ids_after_resume["script_generation"] == child_runs["script_generation"].id
    assert await external_counts() == {
        "provider": 4,
        "tool": 4,
        "expert": 4,
        "durable_tool": 4,
    }

    visual_child_id = resumed.report["interrupt"]["child_skill_run_id"]
    await accept_artifact(session, admin, artifact_id=visual_node["artifact_id"])
    visual_child = await session.get(SkillRun, visual_child_id)
    assert visual_child is not None and visual_child.status == "completed"
    external_after_visual_accept = await external_counts()
    await accept_artifact(session, admin, artifact_id=visual_node["artifact_id"])
    await session.refresh(visual_child)
    assert visual_child.id == visual_child_id and visual_child.status == "completed"
    assert await external_counts() == external_after_visual_accept
    await session.refresh(run)
    await session.refresh(parent_run)
    claimed = await acquire_agent_run(
        session,
        run.id,
        worker_id="operation-test-worker",
        lease_seconds=60,
    )
    assert claimed is not None
    calendar_result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=claimed,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=claimed,
            skill_code="operation_iteration",
            structured_input={
                "confirmed_review_artifact_id": review.id,
                "cycle_days": 7,
                "topic_count": 4,
                "script_duration_seconds": 30,
            },
        ),
        lease_owner="operation-test-worker",
        resume_skill_run=parent_run,
    )
    calendar_nodes = {
        node["skill_code"]: node
        for node in calendar_result.report["child_skill_graph"]
    }
    calendar_node = calendar_nodes["content_calendar_planning"]
    assert calendar_result.status == "waiting_user"
    assert calendar_node["status"] == "needs_review"
    assert runtime.child_requests[-1].requested_skill_code == "content_calendar_planning"
    assert runtime.child_requests[-1].structured_input == {
        "source_artifact_ids": [visual_node["artifact_id"]],
        "days": 7,
    }
    publishing_node = calendar_nodes["publishing_preparation"]
    assert publishing_node["status"] == "pending"
    assert calendar_result.report["interrupt"]["kind"] == "child_skill_paused"
    assert calendar_result.report["interrupt"]["skill_code"] == (
        "content_calendar_planning"
    )
    assert isinstance(calendar_result.report["interrupt"]["child_skill_run_id"], int)
    assert calendar_result.report["interrupt"]["source_artifact_ids"] == [
        calendar_node["artifact_id"]
    ]
    assert await external_counts() == {
        "provider": 5,
        "tool": 5,
        "expert": 5,
        "durable_tool": 5,
    }

    await accept_artifact(session, admin, artifact_id=calendar_node["artifact_id"])
    await session.refresh(run)
    await session.refresh(parent_run)
    claimed = await acquire_agent_run(
        session,
        run.id,
        worker_id="operation-test-worker",
        lease_seconds=60,
    )
    assert claimed is not None
    publishing_result = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=claimed,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=claimed,
            skill_code="operation_iteration",
            structured_input={
                "confirmed_review_artifact_id": review.id,
                "cycle_days": 7,
                "topic_count": 4,
                "script_duration_seconds": 30,
            },
        ),
        lease_owner="operation-test-worker",
        resume_skill_run=parent_run,
    )
    publishing_nodes = {
        node["skill_code"]: node
        for node in publishing_result.report["child_skill_graph"]
    }
    publishing_node = publishing_nodes["publishing_preparation"]
    assert publishing_result.status == "waiting_permission"
    assert publishing_node["status"] == "waiting_permission"
    assert runtime.child_requests[-1].requested_skill_code == "publishing_preparation"
    assert runtime.child_requests[-1].structured_input == {
        "content_item_id": task.content_item_id
    }
    assert await external_counts() == {
        "provider": 6,
        "tool": 6,
        "expert": 6,
        "durable_tool": 6,
    }
    publishing_child = await session.scalar(
        select(SkillRun).where(
            SkillRun.run_id == run.id,
            SkillRun.skill_code == "publishing_preparation",
        )
    )
    assert publishing_child is not None
    approval_call = await session.scalar(
        select(AgentToolCall).where(
            AgentToolCall.skill_run_id == publishing_child.id,
            AgentToolCall.status == "waiting_approval",
        )
    )
    assert approval_call is not None
    approval_call.status = "success"
    approval_commit_spy = AsyncMock(wraps=session.commit)
    monkeypatch.setattr(session, "commit", approval_commit_spy)
    finish_result = await finalize_skill_finish_approval(
        session,
        tool_call=approval_call,
        task=task,
        approved=True,
        comment="approved",
    )
    assert finish_result.handled is True
    assert finish_result.publish_intents == ()
    approval_commit_spy.assert_not_awaited()
    await session.commit()
    assert approval_commit_spy.await_count == 1
    await session.refresh(run)
    await session.refresh(parent_run)
    await session.refresh(publishing_child)
    assert publishing_child.status == "completed"
    assert parent_run.status == "running"
    assert run.status == "queued"

    plan_deliverables = list(
        await session.scalars(
            select(Deliverable).where(
                Deliverable.skill_run_id == parent_run.id,
                Deliverable.agent_code == AgentCode.DECISION.value,
            )
        )
    )
    assert plan_deliverables == []
    assert parent_run.output_snapshot["report"]["child_skill_graph"][-1]["status"] == (
        "waiting_permission"
    )

    external_counts_before_finish = (len(tools.calls), len(harness.calls))
    claimed = await acquire_agent_run(
        session,
        run.id,
        worker_id="operation-test-worker",
        lease_seconds=60,
    )
    assert claimed is not None
    completed = await runtime.execute(
        session,
        user=admin,
        thread=thread,
        turn=turn,
        run=claimed,
        skill_code="operation_iteration",
        capability_request=_capability_request(
            admin=admin,
            account=account,
            thread=thread,
            turn=turn,
            run=claimed,
            skill_code="operation_iteration",
            structured_input={
                "confirmed_review_artifact_id": review.id,
                "cycle_days": 7,
                "topic_count": 4,
                "script_duration_seconds": 30,
            },
        ),
        lease_owner="operation-test-worker",
        resume_skill_run=parent_run,
    )
    assert completed.status == "completed"
    assert completed.report["required_children_completed"] is True
    assert all(
        node["status"] == "completed"
        for node in completed.report["child_skill_graph"]
        if node["required"]
    )
    assert (len(tools.calls), len(harness.calls)) == external_counts_before_finish
    assert await external_counts() == {
        "provider": 6,
        "tool": 6,
        "expert": 6,
        "durable_tool": 6,
    }
    await session.refresh(run)
    await session.refresh(parent_run)
    await session.refresh(task)
    await session.refresh(turn)
    assert run.status == "completed"
    assert parent_run.status == "completed"
    assert task.status == BrainTaskStatus.COMPLETED
    assert task.progress == 100
    assert turn.status == "completed"
    terminal_events = list(
        await session.scalars(
            select(Event).where(
                Event.turn_id == turn.id,
                Event.type == "turn.completed",
            )
        )
    )
    assert len(terminal_events) == 1
    final_plan_deliverables = list(
        await session.scalars(
            select(Deliverable).where(
                Deliverable.skill_run_id == parent_run.id,
                Deliverable.agent_code == AgentCode.DECISION.value,
            )
        )
    )
    assert len(final_plan_deliverables) == 1
    assert final_plan_deliverables[0].payload["required_children_completed"] is True
    assert all(
        node["status"] == "completed"
        for node in final_plan_deliverables[0].payload["child_skill_graph"]
        if node["required"]
    )


@pytest.mark.asyncio
async def test_composite_accept_before_pause_recheck_never_loses_wakeup(
    session,
    admin,
    monkeypatch,
) -> None:
    from app.services.composite_skill_runs import (
        pause_composite_parent_for_artifacts,
    )
    from tests.test_operation_iteration_skill import _artifact

    account, thread, turn, run = await _scope(
        session,
        admin,
        key="composite-accept-before-pause",
        message="根据复盘安排下周运营",
    )
    review = await _artifact(
        session,
        admin,
        account,
        kind=DeliverableType.REVIEW_REPORT,
        status=DeliverableStatus.APPROVED,
    )
    result = await SkillRuntime(tool_executor=_Tools(), harness=_Harness()).execute(
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
            structured_input={"confirmed_review_artifact_id": review.id},
        ),
    )
    parent = await session.get(SkillRun, result.skill_run_id)
    task = await session.get(BrainTask, result.task_id)
    script_node = next(
        node
        for node in result.report["child_skill_graph"]
        if node["skill_code"] == "script_generation"
    )
    assert parent is not None and task is not None
    parent.status = "running"
    run.status = "running"
    turn.status = "running"
    task.status = BrainTaskStatus.RUNNING
    await session.commit()

    maker = async_sessionmaker(session.bind, expire_on_commit=False)
    async with maker() as approval_session:
        concurrent_admin = await approval_session.get(type(admin), admin.id)
        assert concurrent_admin is not None
        await accept_artifact(
            approval_session,
            concurrent_admin,
            artifact_id=script_node["artifact_id"],
        )

    await session.refresh(parent)
    await session.refresh(run)
    commit_spy = AsyncMock(wraps=session.commit)
    monkeypatch.setattr(session, "commit", commit_spy)
    paused = await pause_composite_parent_for_artifacts(
        session,
        parent_skill_run=parent,
        source_artifact_ids=[script_node["artifact_id"]],
    )

    assert paused is False
    assert parent.status == "running"
    assert run.status == "running"
    commit_spy.assert_not_awaited()


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


async def _nested_finish_approval_scope(session, admin, *, key: str):
    account, thread, turn, run = await _scope(
        session,
        admin,
        key=key,
        message="Prepare the nested publishing package",
    )
    content = ContentItem(
        account_id=account.id,
        created_by_id=admin.id,
        title=f"content-{key}",
        current_stage=ContentStage.OPERATION,
        status=ContentStatus.IN_PROGRESS,
    )
    session.add(content)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content.id,
        title=f"task-{key}",
        status=BrainTaskStatus.PENDING_CONFIRMATION,
        progress=90,
        current_focus="Waiting for nested publishing approval",
        runtime_mode="skill",
    )
    session.add(task)
    await session.flush()
    run.task_id = task.id
    run.status = "waiting_permission"
    run.phase = "waiting_permission"
    turn.status = "waiting_permission"
    parent = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=f"{key}:parent",
        skill_code="operation_iteration",
        skill_version=1,
        status="waiting_permission",
        input_snapshot={},
        output_snapshot={
            "status": "waiting_permission",
            "report": {
                "required_children_completed": False,
                "child_skill_graph": [
                    {
                        "skill_code": "publishing_preparation",
                        "status": "waiting_permission",
                    }
                ],
                "interrupt": {
                    "kind": "child_skill_paused",
                    "skill_code": "publishing_preparation",
                    "source_artifact_ids": [],
                },
            },
        },
    )
    session.add(parent)
    await session.flush()
    child = SkillRun(
        org_id=admin.org_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        task_id=task.id,
        idempotency_key=f"{key}:child",
        skill_code="publishing_preparation",
        skill_version=1,
        status="waiting_permission",
        input_snapshot={},
        output_snapshot={
            "status": "waiting_permission",
            "artifact_type": "publish_package",
            "report": {"summary": "Ready for approval"},
            "composite_parent_skill_run_id": parent.id,
        },
    )
    session.add(child)
    await session.flush()
    artifact = Deliverable(
        content_item_id=content.id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        skill_run_id=child.id,
        agent_code=AgentCode.OPERATOR.value,
        type=DeliverableType.REVIEW_REPORT,
        version=1,
        status=DeliverableStatus.PENDING_REVIEW,
        payload={"summary": "Ready for approval"},
    )
    session.add(artifact)
    await session.flush()
    call = AgentToolCall(
        org_id=admin.org_id,
        task_id=task.id,
        skill_run_id=child.id,
        thread_id=thread.id,
        turn_id=turn.id,
        tool_code="nested_finish_approval",
        tool_name="Nested finish approval",
        idempotency_key=f"{key}:approval",
        side_effect_level="read",
        status="waiting_approval",
        permission_mode="confirm",
        requires_human_confirmation=True,
        meta={"approval_stage": "before_finish", "artifact_id": artifact.id},
    )
    session.add(call)
    await session.commit()
    return turn, run, task, parent, child, artifact, call


@pytest.mark.asyncio
async def test_nested_finish_rejection_commits_once_then_replays_stable_events(
    client, session, admin, monkeypatch
) -> None:
    turn, run, task, parent, child, artifact, call = await _nested_finish_approval_scope(
        session, admin, key="nested-reject-transaction"
    )
    published: list[int] = []

    async def capture_publish(_event_type, _payload, **kwargs) -> None:
        published.append(kwargs["event_id"])

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime.publish_realtime_event", capture_publish
    )
    login = await client.post(
        "/auth/login",
        json={"email": "admin@test.com", "password": "admin-pw-123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    payload = {"approved": False, "comment": "Reject the required child"}

    first = await client.post(
        f"/brain/tool-calls/{call.id}/approve", headers=headers, json=payload
    )

    assert first.status_code == 200
    await session.refresh(run)
    await session.refresh(turn)
    await session.refresh(task)
    await session.refresh(parent)
    await session.refresh(child)
    await session.refresh(artifact)
    await session.refresh(call)
    assert (run.status, turn.status, task.status) == (
        "blocked",
        "blocked",
        BrainTaskStatus.FAILED,
    )
    assert (parent.status, child.status) == ("blocked", "blocked")
    assert artifact.status is DeliverableStatus.REJECTED
    assert call.status == "failed"
    durable_runtime_events = list(
        await session.scalars(
            select(Event)
            .where(Event.turn_id == turn.id, Event.type.like("brain.runtime.%"))
            .order_by(Event.id)
        )
    )
    durable_ids = [event.id for event in durable_runtime_events]
    assert durable_ids
    assert published == durable_ids
    event_count = await session.scalar(
        select(func.count(Event.id)).where(Event.turn_id == turn.id)
    )

    duplicate = await client.post(
        f"/brain/tool-calls/{call.id}/approve", headers=headers, json=payload
    )
    opposite = await client.post(
        f"/brain/tool-calls/{call.id}/approve",
        headers=headers,
        json={"approved": True, "comment": "Conflicting retry"},
    )

    assert duplicate.status_code == 200
    assert opposite.status_code == 409
    assert published == durable_ids + durable_ids
    assert await session.scalar(
        select(func.count(Event.id)).where(Event.turn_id == turn.id)
    ) == event_count


@pytest.mark.asyncio
async def test_nested_finish_rejection_rollback_publishes_nothing(
    session, admin, monkeypatch
) -> None:
    from app.services.composite_skill_runs import lock_composite_finish_approval
    from app.services.runtime_state import publish_runtime_state_intents

    turn, run, task, parent, child, artifact, call = await _nested_finish_approval_scope(
        session, admin, key="nested-reject-rollback"
    )
    published: list[int] = []

    async def capture_publish(_event_type, _payload, **kwargs) -> None:
        published.append(kwargs["event_id"])

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime.publish_realtime_event", capture_publish
    )
    locked_call = await lock_composite_finish_approval(session, tool_call=call)
    locked_call.status = "failed"
    locked_call.meta = {
        **dict(locked_call.meta or {}),
        "decision": {"approved": False, "comment": "crash before commit"},
    }
    result = await finalize_skill_finish_approval(
        session,
        tool_call=locked_call,
        task=task,
        approved=False,
        comment="crash before commit",
    )

    assert result.handled is True
    assert result.publish_intents
    assert all(
        isinstance(intent.event_id, int)
        and isinstance(intent.event_type, str)
        and (intent.turn_id is None or isinstance(intent.turn_id, int))
        for intent in result.publish_intents
    )
    assert published == []
    await session.rollback()
    for row in (run, turn, task, parent, child, artifact, call):
        await session.refresh(row)
    assert (run.status, turn.status, task.status) == (
        "waiting_permission",
        "waiting_permission",
        BrainTaskStatus.PENDING_CONFIRMATION,
    )
    assert (parent.status, child.status) == (
        "waiting_permission",
        "waiting_permission",
    )
    assert artifact.status is DeliverableStatus.PENDING_REVIEW
    assert call.status == "waiting_approval"
    await publish_runtime_state_intents(session, result.publish_intents)
    assert published == []
    assert await session.scalar(
        select(func.count(Event.id)).where(Event.turn_id == turn.id)
    ) == 0


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
    monkeypatch,
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

    import app.api.brain as brain_api

    original_scope_lock = brain_api.lock_composite_finish_approval
    approval_scope_checked = False

    async def tracked_scope_lock(*args, **kwargs):
        nonlocal approval_scope_checked
        result = await original_scope_lock(*args, **kwargs)
        approval_scope_checked = True
        return result

    def reject_prelock_autoflush(*_args, **_kwargs) -> None:
        assert approval_scope_checked, "approval rows autoflushed before scope lock"

    monkeypatch.setattr(brain_api, "lock_composite_finish_approval", tracked_scope_lock)
    event.listen(session.sync_session, "before_flush", reject_prelock_autoflush)

    response = await client.post(
        f"/brain/tool-calls/{call.id}/approve",
        headers=headers,
        json={"approved": approved, "comment": "人工审批结论"},
    )

    assert response.status_code == 200
    assert approval_scope_checked
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
