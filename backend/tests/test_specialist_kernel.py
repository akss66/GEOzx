from unittest.mock import AsyncMock

import pytest

from app.orchestrator.agent_kernel import (
    KernelAction,
    KernelEventType,
    SpecialistKernelDecision,
    expert_kernel_policy,
    main_kernel_policy,
)
from app.orchestrator.specialist_kernel import SpecialistKernel
from app.schemas.brain import RuntimeToolCall


@pytest.mark.asyncio
async def test_specialist_kernel_runs_one_compatible_round() -> None:
    runner = AsyncMock()
    runner.run.return_value = object()
    context = object()
    kernel = SpecialistKernel()

    result = await kernel.run(
        AsyncMock(),
        org_id=7,
        runner=runner,
        context=context,
        policy=expert_kernel_policy(tool_allowlist={"account.profile"}),
    )

    assert result.payload is runner.run.return_value
    assert result.rounds == 1
    assert result.tool_calls == 0
    runner.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_specialist_kernel_rejects_main_agent_policy() -> None:
    kernel = SpecialistKernel()

    with pytest.raises(ValueError, match="specialist policy"):
        await kernel.run(
            AsyncMock(),
            org_id=7,
            runner=AsyncMock(),
            context=object(),
            policy=main_kernel_policy(),
        )


@pytest.mark.asyncio
async def test_specialist_kernel_feeds_tool_observation_into_next_round() -> None:
    deliverable = object()
    runner = AsyncMock()
    runner.kernel_decide.side_effect = [
        SpecialistKernelDecision(
            action=KernelAction.CALL_TOOLS,
            rationale="Need current account facts.",
            tool_calls=(
                RuntimeToolCall(
                    tool_code="account.profile",
                    arguments={},
                    purpose="Read the selected account profile.",
                    idempotency_key="task-1:profile",
                ),
            ),
        ),
        SpecialistKernelDecision(
            action=KernelAction.FINISH,
            rationale="The evidence is sufficient.",
            deliverable=deliverable,
        ),
    ]
    execute_tool = AsyncMock(return_value={"nickname": "Demo"})
    events: list[KernelEventType] = []

    async def emit_event(event_type, _payload) -> None:
        events.append(event_type)

    result = await SpecialistKernel().run(
        AsyncMock(),
        org_id=7,
        runner=runner,
        context=object(),
        policy=expert_kernel_policy(tool_allowlist={"account.profile"}),
        available_tools=[{"code": "account.profile"}],
        execute_tool=execute_tool,
        emit_event=emit_event,
    )

    assert result.payload is deliverable
    assert result.rounds == 2
    assert result.tool_calls == 1
    execute_tool.assert_awaited_once()
    observations = runner.kernel_decide.await_args_list[1].kwargs["observations"]
    assert observations == [
        {
            "tool_code": "account.profile",
            "purpose": "Read the selected account profile.",
            "result": {"nickname": "Demo"},
        }
    ]
    assert events == [
        KernelEventType.AGENT_START,
        KernelEventType.TURN_START,
        KernelEventType.DECISION,
        KernelEventType.TOOL_START,
        KernelEventType.TOOL_END,
        KernelEventType.TURN_END,
        KernelEventType.TURN_START,
        KernelEventType.DECISION,
        KernelEventType.TURN_END,
        KernelEventType.AGENT_END,
    ]
