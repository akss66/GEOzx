"""Unified expert harness contracts."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.agents.base import AgentContext, BaseAgent
from app.agents.registry import AGENT_SPECS
from app.models import (
    Account,
    AgentInvocation,
    AgentRun,
    AgentToolCall,
    BrainTask,
    Client,
    ContentItem,
    Deliverable,
    Event,
    OrchestrationPlan,
    Project,
    TaskBrief,
)
from app.models.enums import (
    AgentCode,
    AgentInvocationStatus,
    BrainTaskStatus,
    BrainTaskType,
    ContentStatus,
    DeliverableType,
    Platform,
)
from app.orchestrator.agent_harness import AgentHarness, AgentHarnessError
from app.orchestrator.agent_kernel import KernelAction, SpecialistKernelDecision
from app.orchestrator.brain_runtime import BrainRuntimeGraph, bind_runtime_session
from app.schemas.brain import AgentInvocationOut, RuntimeToolCall
from app.schemas.deliverable import (
    AdPlanPayload,
    DeliverablePayload,
    PositioningStrategyPayload,
)


def test_agent_registry_contains_every_specialist() -> None:
    assert set(AGENT_SPECS) == {
        AgentCode.POSITIONING,
        AgentCode.CONTENT_DIRECTOR,
        AgentCode.ART_DIRECTOR,
        AgentCode.VIDEO_CREATOR,
        AgentCode.EDITOR,
        AgentCode.OPERATOR,
        AgentCode.ADVERTISER,
        AgentCode.CUSTOMER_SERVICE,
    }


def test_harness_only_exposes_auto_runtime_tools_to_specialists() -> None:
    assert AgentHarness._autonomous_runtime_tool_codes(
        {
            "tool_permissions": {
                "account_context": "auto",
                "profile_snapshot": "auto",
                "review_metrics": "confirm",
                "knowledge_search": "auto",
            }
        }
    ) == {"account.profile", "account.data_context"}


@pytest.mark.asyncio
async def test_harness_runs_positioning_with_one_account_without_project(
    session, admin, monkeypatch
) -> None:
    account = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Projectless positioning account",
        auth={"auth_status": "authorized"},
    )
    session.add(account)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Projectless positioning diagnosis",
        type=BrainTaskType.REVIEW_OPTIMIZATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
        thread_id="projectless-positioning-thread",
    )
    task.brief = TaskBrief(
        goal="Diagnose this account's positioning",
        project_id=None,
        project_name=None,
        platforms=[Platform.DOUYIN.value],
        account_ids=[account.id],
        cycle="current",
        content_goal="positioning diagnosis",
        risk_constraints=[],
        expected_outputs=["positioning diagnosis"],
        confirmation_actions=[],
    )
    task.plan = OrchestrationPlan(
        summary="diagnosis only",
        steps=[],
        quality_gates=[],
        estimated_cost=Decimal("0"),
        requires_human_confirmation=True,
    )
    session.add(task)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        client_message_id="agent-run-38",
        request_payload={},
        result_payload={},
    )
    session.add(run)
    await session.commit()

    contexts: list[AgentContext] = []

    class ToolCallingPositioningAgent(BaseAgent):
        code = AgentCode.POSITIONING.value
        output_type = DeliverableType.POSITIONING_STRATEGY

        async def run(self, runtime_session, org_id, ctx: AgentContext):
            raise AssertionError("The bounded tool loop should call kernel_decide instead")

        async def kernel_decide(
            self, runtime_session, org_id, ctx: AgentContext, *, available_tools, observations
        ):
            contexts.append(ctx)
            if not observations:
                return SpecialistKernelDecision(
                    action=KernelAction.CALL_TOOLS,
                    rationale="Read the selected account profile before diagnosing positioning.",
                    tool_calls=(
                        RuntimeToolCall(
                            tool_code="account.profile",
                            arguments={},
                            purpose="Load the selected account profile.",
                            idempotency_key="agent-run-38:account-profile",
                        ),
                    ),
                )
            assert observations[0]["result"]["account_id"] == account.id
            return SpecialistKernelDecision(
                action=KernelAction.FINISH,
                rationale="The account profile is sufficient for this diagnosis.",
                deliverable=PositioningStrategyPayload(
                    account_persona="Practical creator",
                    target_audience="Early-stage operators",
                    differentiation=["Evidence-led", "Actionable"],
                    content_pillars=["Account diagnosis", "Operating playbooks"],
                ),
            )

    async def fake_business_config(*_args, **_kwargs):
        return {
            "tool_permissions": {
                "account_context": "auto",
                "profile_snapshot": "confirm",
                "review_metrics": "confirm",
            },
            "quality_gates": [],
        }

    monkeypatch.setattr(
        "app.orchestrator.agent_harness.get_business_config", fake_business_config
    )

    original = AGENT_SPECS[AgentCode.POSITIONING]
    monkeypatch.setitem(
        AGENT_SPECS,
        AgentCode.POSITIONING,
        original.__class__(
            original.name,
            ToolCallingPositioningAgent,
            original.deliverable_type,
            original.deliverable_title,
            original.stage,
            original.task_type,
        ),
    )
    upstream = {
        "tool_results": {
            "items": [
                {
                    "tool_code": "account.profile",
                    "result": {"account_id": account.id, "nickname": account.nickname},
                },
                {
                    "tool_code": "account.data_context",
                    "result": {"account_id": account.id, "coverage": "complete"},
                },
            ]
        }
    }

    result = await AgentHarness().execute(
        session,
        user=admin,
        task=task,
        code=AgentCode.POSITIONING,
        purpose="Diagnosis only",
        evidence_refs=["account-profile:38", "account-data-context:38"],
        upstream=upstream,
        run_id=run.id,
        step_key="round-1:01-positioning",
    )

    assert result.invocation.status == AgentInvocationStatus.DONE
    assert result.deliverable.content_item_id == result.task.content_item_id
    assert result.task.content_item_id is not None
    content_item = await session.get(ContentItem, result.task.content_item_id)
    assert content_item is not None
    assert content_item.project_id is None
    assert content_item.account_id == account.id
    assert contexts[0].project_id is None
    assert contexts[0].account_id == account.id
    assert contexts[0].upstream["tool_results"] == upstream["tool_results"]
    tool_calls = (
        await session.scalars(
            select(AgentToolCall).where(AgentToolCall.task_id == task.id)
        )
    ).all()
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_code == "account.profile"
    assert tool_calls[0].status == "success"
    tool_events = (
        await session.scalars(
            select(Event).where(Event.type == "agent.kernel.tool_end")
        )
    ).all()
    assert len(tool_events) == 1
    assert tool_events[0].project_id is None
    assert result.acceptance.brain_rejudge_basis[0] == (
        "The result is scoped to the selected account."
    )
    deliverable_count = await session.scalar(
        select(func.count(Deliverable.id)).where(
            Deliverable.content_item_id == result.deliverable.content_item_id,
            Deliverable.agent_code == result.deliverable.agent_code,
            Deliverable.type == result.deliverable.type,
            Deliverable.version == result.deliverable.version,
        )
    )
    assert deliverable_count == 1
    completed_events = [
        event
        for event in (
            await session.scalars(
                select(Event).where(
                    Event.type == "agent.harness.completed",
                    Event.content_item_id == result.deliverable.content_item_id,
                )
            )
        ).all()
        if event.payload is not None
        and event.payload.get("task_id") == task.id
        and event.payload.get("run_id") == run.id
        and event.payload.get("agent_code") == AgentCode.POSITIONING.value
    ]
    assert len(completed_events) == 1
    assert completed_events[0].payload["invocation_id"] == result.invocation.id
    assert completed_events[0].payload["deliverable_id"] == result.deliverable.id
    lifecycle_events = [
        event
        for event in (
            await session.scalars(
                select(Event).where(
                    Event.type.in_(
                        [
                            "brain.runtime.subagent_started",
                            "brain.runtime.subagent_completed",
                        ]
                    )
                ).order_by(Event.id)
            )
        ).all()
        if event.payload is not None
        and event.payload.get("invocation_id") == result.invocation.id
    ]
    assert [event.type for event in lifecycle_events] == [
        "brain.runtime.subagent_started",
        "brain.runtime.subagent_completed",
    ]
    assert all(event.payload["invocation_id"] == result.invocation.id for event in lifecycle_events)
    assert all(event.idempotency_key is not None for event in lifecycle_events)
    invocations = (
        await session.scalars(
            select(AgentInvocation).where(AgentInvocation.task_id == task.id)
        )
    ).all()
    assert [invocation.id for invocation in invocations] == [result.invocation.id]


@pytest.mark.asyncio
async def test_harness_reuses_the_same_invocation_for_an_idempotent_retry(
    session, admin, monkeypatch
) -> None:
    client = Client(org_id=admin.org_id, name="Harness client")
    project = Project(org_id=admin.org_id, client=client, name="Harness project")
    session.add_all([client, project])
    await session.flush()
    account = Account(
        org_id=admin.org_id,
        client_id=client.id,
        project_id=project.id,
        platform=Platform.DOUYIN,
        nickname="Harness account",
        auth={"auth_status": "authorized"},
    )
    session.add(account)
    await session.flush()
    content_item = ContentItem(
        project_id=project.id,
        created_by_id=admin.id,
        account_id=account.id,
        title="Harness content",
        status=ContentStatus.IN_PROGRESS,
    )
    session.add(content_item)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        content_item_id=content_item.id,
        title="Harness task",
        type=BrainTaskType.REVIEW_OPTIMIZATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
        thread_id="harness-thread-1",
    )
    task.brief = TaskBrief(
        goal="Prepare a controlled ad plan",
        project_id=project.id,
        project_name=project.name,
        platforms=[Platform.DOUYIN.value],
        account_ids=[account.id],
        cycle="current",
        content_goal="ad plan",
        risk_constraints=[],
        expected_outputs=["ad plan"],
        confirmation_actions=[],
    )
    task.plan = OrchestrationPlan(
        summary="dynamic",
        steps=[],
        quality_gates=[],
        estimated_cost=Decimal("0"),
        requires_human_confirmation=True,
    )
    session.add(task)
    await session.flush()
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        task_id=task.id,
        client_message_id="harness-message-1",
        request_payload={},
        result_payload={},
    )
    session.add(run)
    await session.commit()

    calls: list[AgentContext] = []

    class FakeAdvertisingAgent(BaseAgent):
        code = AgentCode.ADVERTISER.value
        output_type = DeliverableType.AD_PLAN

        async def run(
            self, runtime_session, org_id: int | None, ctx: AgentContext
        ) -> DeliverablePayload:
            calls.append(ctx)
            return AdPlanPayload(
                objective="Validate demand safely",
                target_audience="Existing followers",
                budget_strategy="No spend without approval",
                creative_directions=[
                    "Reuse approved organic content",
                    "Compare two validated hooks",
                ],
                risk_controls=["Manual approval", "Stop at the approved spend cap"],
                measurement={"primary": "qualified engagement"},
            )

    original = AGENT_SPECS[AgentCode.ADVERTISER]
    monkeypatch.setitem(
        AGENT_SPECS,
        AgentCode.ADVERTISER,
        original.__class__(
            original.name,
            FakeAdvertisingAgent,
            original.deliverable_type,
            original.deliverable_title,
            original.stage,
            original.task_type,
        ),
    )

    harness = AgentHarness()
    first = await harness.execute(
        session,
        user=admin,
        task=task,
        code=AgentCode.ADVERTISER,
        purpose="Evaluate a paid-growth option",
        evidence_refs=["account-profile:1"],
        run_id=run.id,
        step_key="round-1:07-advertiser",
        attempt=1,
    )
    retry = await harness.execute(
        session,
        user=admin,
        task=task,
        code=AgentCode.ADVERTISER,
        purpose="Evaluate a paid-growth option",
        evidence_refs=["account-profile:1"],
        run_id=run.id,
        step_key="round-1:07-advertiser",
        attempt=1,
    )

    assert retry.invocation.id == first.invocation.id
    assert retry.deliverable.id == first.deliverable.id
    assert len(calls) == 1
    assert calls[0].task_id == task.id
    assert calls[0].invocation_id == first.invocation.id
    assert calls[0].project_id == project.id
    assert calls[0].account_id == account.id
    assert calls[0].trace_id == f"agent-run:{run.id}"
    invocation_out = AgentInvocationOut.model_validate(first.invocation)
    assert invocation_out.run_id == run.id
    assert invocation_out.step_key == "round-1:07-advertiser"
    assert invocation_out.attempt == 1

    class FailingAdvertisingAgent(BaseAgent):
        code = AgentCode.ADVERTISER.value
        output_type = DeliverableType.AD_PLAN

        async def run(self, runtime_session, org_id, ctx):
            raise RuntimeError("provider unavailable")

    monkeypatch.setitem(
        AGENT_SPECS,
        AgentCode.ADVERTISER,
        original.__class__(
            original.name,
            FailingAdvertisingAgent,
            original.deliverable_type,
            original.deliverable_title,
            original.stage,
            original.task_type,
        ),
    )
    run_id = run.id
    with pytest.raises(AgentHarnessError):
        await harness.execute(
            session,
            user=admin,
            task=task,
            code=AgentCode.ADVERTISER,
            purpose="Exercise the durable failure ledger",
            evidence_refs=[],
            run_id=run_id,
            step_key="round-2:07-advertiser",
            attempt=1,
        )
    failed = await session.scalar(
        select(AgentInvocation).where(
            AgentInvocation.run_id == run_id,
            AgentInvocation.step_key == "round-2:07-advertiser",
        )
    )
    assert failed is not None
    assert failed.status == AgentInvocationStatus.FAILED
    assert failed.failure_reason == "RuntimeError"


@pytest.mark.asyncio
async def test_runtime_dispatches_growth_expert_through_the_shared_harness(
    session, admin, monkeypatch
) -> None:
    client = Client(org_id=admin.org_id, name="Runtime client")
    project = Project(org_id=admin.org_id, client=client, name="Runtime project")
    session.add_all([client, project])
    await session.flush()
    account = Account(
        org_id=admin.org_id,
        client_id=client.id,
        project_id=project.id,
        platform=Platform.DOUYIN,
        nickname="Runtime account",
        auth={"auth_status": "authorized"},
    )
    session.add(account)
    await session.flush()
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Runtime task",
        type=BrainTaskType.REVIEW_OPTIMIZATION,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
        thread_id="runtime-harness-thread",
    )
    task.brief = TaskBrief(
        goal="Assess growth options",
        project_id=project.id,
        project_name=project.name,
        platforms=[Platform.DOUYIN.value],
        account_ids=[account.id],
        cycle="current",
        content_goal="growth",
        risk_constraints=[],
        expected_outputs=[],
        confirmation_actions=[],
    )
    task.plan = OrchestrationPlan(
        summary="dynamic",
        steps=[],
        quality_gates=[],
        estimated_cost=Decimal("0"),
        requires_human_confirmation=True,
    )
    session.add(task)
    await session.commit()

    captured: list[dict] = []

    async def fake_execute(runtime_session, **kwargs):
        captured.append(kwargs)

    monkeypatch.setattr("app.orchestrator.brain_runtime.agent_harness.execute", fake_execute)
    runtime = BrainRuntimeGraph()
    with bind_runtime_session(session):
        state = await runtime._dispatch_round(
            {
                "task_id": task.id,
                "agent_run_id": 42,
                "agent_run_attempt": 2,
                "round_index": 3,
                "selected_experts": [AgentCode.ADVERTISER.value],
                "selected_expert_purpose": "Assess paid growth",
                "selected_expert_evidence_refs": ["metrics:7"],
                "observations": [
                    {
                        "kind": "tool_result",
                        "tool_call_id": 7,
                        "tool_code": "account.profile",
                        "summary": "profile loaded",
                        "result": {"account_id": account.id, "nickname": account.nickname},
                    },
                    {
                        "kind": "tool_result",
                        "tool_call_id": 8,
                        "tool_code": "account.data_context",
                        "summary": "data context loaded",
                        "result": {"account_id": account.id, "coverage": "complete"},
                    },
                ],
            }
        )

    assert state["status"] == "round_dispatched"
    assert len(captured) == 1
    assert captured[0]["user"].id == admin.id
    assert captured[0]["task"].id == task.id
    assert captured[0]["code"] == AgentCode.ADVERTISER
    assert captured[0]["run_id"] == 42
    assert captured[0]["attempt"] == 2
    assert captured[0]["step_key"] == "round-3:07-advertiser"
    assert captured[0]["purpose"] == "Assess paid growth"
    assert captured[0]["evidence_refs"] == ["metrics:7"]
    assert captured[0]["upstream"] == {
        "tool_results": {
            "items": [
                {
                    "kind": "tool_result",
                    "tool_call_id": 7,
                    "tool_code": "account.profile",
                    "summary": "profile loaded",
                    "result": {"account_id": account.id, "nickname": account.nickname},
                },
                {
                    "kind": "tool_result",
                    "tool_call_id": 8,
                    "tool_code": "account.data_context",
                    "summary": "data context loaded",
                    "result": {"account_id": account.id, "coverage": "complete"},
                },
            ]
        }
    }
