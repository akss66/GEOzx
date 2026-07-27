"""Bounded execution entry point shared by every specialist Agent."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext, BaseAgent
from app.orchestrator.agent_kernel import (
    AgentKernelPolicy,
    KernelAction,
    KernelActor,
    KernelEventType,
    SpecialistKernelDecision,
)
from app.schemas.brain import RuntimeToolCall
from app.schemas.deliverable import DeliverablePayload

ToolExecutor = Callable[[RuntimeToolCall], Awaitable[dict[str, Any]]]
KernelEventSink = Callable[[KernelEventType, dict[str, Any]], Awaitable[None]]


class SpecialistKernelBlocked(RuntimeError):
    """Raised when a specialist cannot safely finish without main-Agent action."""


@dataclass(frozen=True)
class SpecialistKernelResult:
    payload: DeliverablePayload
    rounds: int
    tool_calls: int


class SpecialistKernel:
    """Run a specialist behind code-enforced policy and budget boundaries.

    The compatibility path is intentionally one model round with no direct
    tools. The same boundary will host the bounded ReAct loop without changing
    AgentHarness or the durable task ledger.
    """

    async def run(
        self,
        session: AsyncSession,
        *,
        org_id: int | None,
        runner: BaseAgent,
        context: AgentContext,
        policy: AgentKernelPolicy,
        available_tools: list[dict[str, Any]] | None = None,
        execute_tool: ToolExecutor | None = None,
        emit_event: KernelEventSink | None = None,
    ) -> SpecialistKernelResult:
        if policy.actor != KernelActor.SPECIALIST:
            raise ValueError("specialist kernel requires a specialist policy")

        tools = list(available_tools or [])
        decide = getattr(runner, "kernel_decide", None)
        await self._emit(
            emit_event,
            KernelEventType.AGENT_START,
            {"actor": policy.actor.value, "tools": [item.get("code") for item in tools]},
        )
        if not tools or decide is None:
            return await self._run_compatibility_round(
                session,
                org_id=org_id,
                runner=runner,
                context=context,
                policy=policy,
                emit_event=emit_event,
            )
        if execute_tool is None:
            raise ValueError("specialist tools require an executor")

        observations: list[dict[str, Any]] = []
        tool_call_count = 0
        for round_index in range(1, policy.max_rounds + 1):
            await self._emit(
                emit_event,
                KernelEventType.TURN_START,
                {"round": round_index, "tool_call_count": tool_call_count},
            )
            policy.assert_budget(
                round_index=round_index,
                tool_call_count=tool_call_count,
            )
            decision: SpecialistKernelDecision = await decide(
                session,
                org_id,
                context,
                available_tools=tools,
                observations=observations,
            )
            await self._emit(
                emit_event,
                KernelEventType.DECISION,
                {
                    "round": round_index,
                    "action": decision.action.value,
                    "rationale": decision.rationale,
                    "tool_codes": [call.tool_code for call in decision.tool_calls],
                },
            )
            policy.authorize(
                decision.action,
                tool_codes=[call.tool_code for call in decision.tool_calls],
            )
            if decision.action == KernelAction.FINISH:
                if decision.deliverable is None:
                    raise ValueError("specialist finish requires a deliverable")
                await self._emit(
                    emit_event,
                    KernelEventType.TURN_END,
                    {"round": round_index, "status": "completed"},
                )
                await self._emit(
                    emit_event,
                    KernelEventType.AGENT_END,
                    {
                        "status": "completed",
                        "rounds": round_index,
                        "tool_calls": tool_call_count,
                    },
                )
                return SpecialistKernelResult(
                    payload=decision.deliverable,
                    rounds=round_index,
                    tool_calls=tool_call_count,
                )
            if decision.action == KernelAction.BLOCKED:
                await self._emit(
                    emit_event,
                    KernelEventType.TURN_END,
                    {"round": round_index, "status": "blocked"},
                )
                await self._emit(
                    emit_event,
                    KernelEventType.AGENT_END,
                    {
                        "status": "blocked",
                        "rounds": round_index,
                        "tool_calls": tool_call_count,
                    },
                )
                raise SpecialistKernelBlocked(
                    decision.blocked_reason or decision.rationale
                )
            if decision.action != KernelAction.CALL_TOOLS:
                raise ValueError(
                    f"unsupported specialist action: {decision.action.value}"
                )

            tool_call_count += len(decision.tool_calls)
            policy.assert_budget(
                round_index=round_index,
                tool_call_count=tool_call_count,
            )
            for call in decision.tool_calls:
                await self._emit(
                    emit_event,
                    KernelEventType.TOOL_START,
                    {
                        "round": round_index,
                        "tool_code": call.tool_code,
                        "purpose": call.purpose,
                    },
                )
                result = await execute_tool(call)
                await self._emit(
                    emit_event,
                    KernelEventType.TOOL_END,
                    {
                        "round": round_index,
                        "tool_code": call.tool_code,
                        "status": "success",
                    },
                )
                observations.append(
                    {
                        "tool_code": call.tool_code,
                        "purpose": call.purpose,
                        "result": result,
                    }
                )
            await self._emit(
                emit_event,
                KernelEventType.TURN_END,
                {
                    "round": round_index,
                    "status": "observed",
                    "tool_call_count": tool_call_count,
                },
            )

        await self._emit(
            emit_event,
            KernelEventType.AGENT_END,
            {
                "status": "blocked",
                "reason": "round_budget_exhausted",
                "rounds": policy.max_rounds,
                "tool_calls": tool_call_count,
            },
        )
        raise SpecialistKernelBlocked("specialist round budget exhausted")

    async def _run_compatibility_round(
        self,
        session: AsyncSession,
        *,
        org_id: int | None,
        runner: BaseAgent,
        context: AgentContext,
        policy: AgentKernelPolicy,
        emit_event: KernelEventSink | None,
    ) -> SpecialistKernelResult:
        round_index = 1
        tool_call_count = 0
        await self._emit(
            emit_event,
            KernelEventType.TURN_START,
            {"round": round_index, "compatibility": True},
        )
        policy.assert_budget(round_index=round_index, tool_call_count=tool_call_count)
        payload = await runner.run(session, org_id, context)
        policy.authorize(KernelAction.FINISH)
        await self._emit(
            emit_event,
            KernelEventType.DECISION,
            {
                "round": round_index,
                "action": KernelAction.FINISH.value,
                "compatibility": True,
            },
        )
        await self._emit(
            emit_event,
            KernelEventType.TURN_END,
            {"round": round_index, "status": "completed"},
        )
        await self._emit(
            emit_event,
            KernelEventType.AGENT_END,
            {"status": "completed", "rounds": 1, "tool_calls": 0},
        )
        return SpecialistKernelResult(
            payload=payload,
            rounds=round_index,
            tool_calls=tool_call_count,
        )

    @staticmethod
    async def _emit(
        sink: KernelEventSink | None,
        event_type: KernelEventType,
        payload: dict[str, Any],
    ) -> None:
        if sink is not None:
            await sink(event_type, payload)


specialist_kernel = SpecialistKernel()
