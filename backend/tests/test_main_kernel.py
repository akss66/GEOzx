import pytest

from app.models import BrainTask, TaskBrief
from app.models.enums import AgentCode, BrainTaskStatus, BrainTaskType
from app.orchestrator.agent_kernel import (
    AgentKernelPolicyError,
    expert_kernel_policy,
    main_kernel_policy,
)
from app.orchestrator.brain_runtime import (
    BrainRuntimeGraph,
    bind_runtime_session,
)
from app.orchestrator.main_kernel import (
    MainKernelActionExecutor,
    MainKernelCancelled,
    MainKernelRoute,
)
from app.schemas.brain import RuntimeNextStep, RuntimeToolCall
from app.services.agent_runs import claim_agent_run, request_agent_run_cancel


def _step(action: str, **updates) -> RuntimeNextStep:
    payload = {
        "action": action,
        "rationale": "test rationale",
        "handoff_message": "test handoff",
        **updates,
    }
    return RuntimeNextStep.model_validate(payload)


@pytest.mark.parametrize(
    ("step", "route", "status"),
    [
        (
            _step("dispatch_experts", expert_codes=[AgentCode.POSITIONING]),
            MainKernelRoute.DISPATCH,
            "dispatch",
        ),
        (
            _step(
                "call_tools",
                tool_calls=[
                    RuntimeToolCall(
                        tool_code="account.profile",
                        arguments={},
                        purpose="load profile",
                        idempotency_key="test-profile",
                    )
                ],
            ),
            MainKernelRoute.TOOLS,
            "tools",
        ),
        (
            _step(
                "request_permission",
                tool_calls=[
                    RuntimeToolCall(
                        tool_code="publish.package",
                        arguments={},
                        purpose="prepare publish package",
                        idempotency_key="test-publish",
                    )
                ],
            ),
            MainKernelRoute.TOOLS,
            "tools",
        ),
        (_step("request_decision", decision_request={
            "id": "decision-1",
            "title": "Choose",
            "summary": "Choose a direction",
            "choices": [
                {
                    "id": "a",
                    "title": "A",
                    "description": "A direction",
                    "benefit": "Fast",
                    "tradeoff": "Narrow",
                },
                {
                    "id": "b",
                    "title": "B",
                    "description": "B direction",
                    "benefit": "Broad",
                    "tradeoff": "Slow",
                },
            ],
        }), MainKernelRoute.DECISION, "waiting_decision"),
        (_step("ask_user"), MainKernelRoute.WAITING, "waiting_user"),
        (_step("respond"), MainKernelRoute.WAITING, "waiting_user"),
        (_step("finish"), MainKernelRoute.FINISH, "finish"),
    ],
)
def test_main_kernel_maps_actions_to_one_runtime_route(
    step: RuntimeNextStep,
    route: MainKernelRoute,
    status: str,
) -> None:
    transition = MainKernelActionExecutor(main_kernel_policy()).prepare(step)

    assert transition.route == route
    assert transition.status == status
    assert transition.action.value == step.action


def test_main_kernel_enforces_the_supplied_policy() -> None:
    executor = MainKernelActionExecutor(
        expert_kernel_policy(tool_allowlist={"account.profile"})
    )
    step = _step("dispatch_experts", expert_codes=[AgentCode.POSITIONING])

    with pytest.raises(AgentKernelPolicyError, match="specialist cannot dispatch"):
        executor.prepare(step)


@pytest.mark.asyncio
async def test_main_kernel_stops_at_turn_boundary_when_run_was_cancelled(
    session,
    admin,
) -> None:
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="kernel-cancel-1",
        request_payload={"message": "stop"},
    )
    await request_agent_run_cancel(session, run.id)

    with pytest.raises(MainKernelCancelled):
        await MainKernelActionExecutor(main_kernel_policy()).check_turn_boundary(
            session,
            {"agent_run_id": run.id},
        )


@pytest.mark.asyncio
async def test_main_kernel_allows_turn_without_a_cancelled_run(session) -> None:
    await MainKernelActionExecutor(main_kernel_policy()).check_turn_boundary(
        session,
        {},
    )


def test_brain_runtime_routes_from_the_kernel_transition() -> None:
    route = BrainRuntimeGraph._route_after_smart_decision(
        {
            "status": "finish",
            "kernel_route": MainKernelRoute.TOOLS.value,
        }
    )

    assert route == "tools"


@pytest.mark.asyncio
async def test_brain_runtime_checks_cancellation_before_dispatching_an_expert(
    session,
    admin,
    monkeypatch,
) -> None:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Cancelled dispatch",
        type=BrainTaskType.ACCOUNT_DIAGNOSIS,
        status=BrainTaskStatus.RUNNING,
        runtime_mode="langgraph",
    )
    task.brief = TaskBrief(
        goal="Diagnose the account",
        platforms=["douyin"],
        account_ids=[],
        cycle="current",
        content_goal="diagnosis",
        risk_constraints=[],
        expected_outputs=[],
        confirmation_actions=[],
    )
    session.add(task)
    await session.commit()
    run, _ = await claim_agent_run(
        session,
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="cancel-before-dispatch",
        request_payload={"message": "stop"},
    )
    await request_agent_run_cancel(session, run.id)

    async def unexpected_execute(*args, **kwargs):
        raise AssertionError("cancelled run must not dispatch an expert")

    monkeypatch.setattr(
        "app.orchestrator.brain_runtime.agent_harness.execute",
        unexpected_execute,
    )

    with bind_runtime_session(session), pytest.raises(MainKernelCancelled):
        await BrainRuntimeGraph()._dispatch_round(
            {
                "task_id": task.id,
                "agent_run_id": run.id,
                "selected_experts": [AgentCode.POSITIONING.value],
                "round_index": 1,
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node_name",
    [
        "_goal_understanding",
        "_situation_awareness",
        "_strategy_planning",
        "_task_planning",
        "_dispatch_round",
        "_execute_tools",
        "_observe_round",
        "_collect_permissions",
        "_decide_next",
        "_smart_permission_gate",
        "_decision_gate",
        "_smart_summarize",
    ],
)
async def test_every_smart_runtime_node_checks_the_turn_boundary(
    node_name,
    monkeypatch,
) -> None:
    runtime = BrainRuntimeGraph()

    async def cancelled(_state):
        raise MainKernelCancelled()

    monkeypatch.setattr(runtime, "_check_main_turn_boundary", cancelled)

    with pytest.raises(MainKernelCancelled):
        await getattr(runtime, node_name)({})
