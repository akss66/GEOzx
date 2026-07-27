"""Single action boundary for the main-Agent runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun
from app.orchestrator.agent_kernel import (
    AgentKernelPolicy,
    KernelAction,
)
from app.schemas.brain import RuntimeNextStep


class MainKernelRoute(StrEnum):
    DISPATCH = "dispatch"
    TOOLS = "tools"
    DECISION = "decision"
    WAITING = "waiting"
    FINISH = "finish"


class MainKernelCancelled(asyncio.CancelledError):
    """Raised when a durable AgentRun was cancelled between turns."""


@dataclass(frozen=True)
class MainKernelTransition:
    action: KernelAction
    route: MainKernelRoute
    status: str


class MainKernelActionExecutor:
    """Authorize model decisions and map them to one runtime transition."""

    _TRANSITIONS = {
        KernelAction.DISPATCH_EXPERTS: MainKernelTransition(
            action=KernelAction.DISPATCH_EXPERTS,
            route=MainKernelRoute.DISPATCH,
            status="dispatch",
        ),
        KernelAction.CALL_TOOLS: MainKernelTransition(
            action=KernelAction.CALL_TOOLS,
            route=MainKernelRoute.TOOLS,
            status="tools",
        ),
        KernelAction.REQUEST_PERMISSION: MainKernelTransition(
            action=KernelAction.REQUEST_PERMISSION,
            route=MainKernelRoute.TOOLS,
            status="tools",
        ),
        KernelAction.REQUEST_DECISION: MainKernelTransition(
            action=KernelAction.REQUEST_DECISION,
            route=MainKernelRoute.DECISION,
            status="waiting_decision",
        ),
        KernelAction.ASK_USER: MainKernelTransition(
            action=KernelAction.ASK_USER,
            route=MainKernelRoute.WAITING,
            status="waiting_user",
        ),
        KernelAction.RESPOND: MainKernelTransition(
            action=KernelAction.RESPOND,
            route=MainKernelRoute.WAITING,
            status="waiting_user",
        ),
        KernelAction.FINISH: MainKernelTransition(
            action=KernelAction.FINISH,
            route=MainKernelRoute.FINISH,
            status="finish",
        ),
    }

    def __init__(self, policy: AgentKernelPolicy) -> None:
        self._policy = policy

    def prepare(self, step: RuntimeNextStep) -> MainKernelTransition:
        action = KernelAction(step.action)
        self._policy.authorize(
            action,
            expert_codes=[code.value for code in step.expert_codes],
            tool_codes=[call.tool_code for call in step.tool_calls],
        )
        transition = self._TRANSITIONS.get(action)
        if transition is None:
            raise ValueError(f"unsupported main-Agent action: {action.value}")
        return transition

    async def check_turn_boundary(
        self,
        session: AsyncSession,
        state: Mapping[str, Any],
    ) -> None:
        run_id = int(state.get("agent_run_id") or 0)
        if run_id <= 0:
            return
        run = await session.get(AgentRun, run_id)
        if run is not None and run.cancel_requested_at is not None:
            raise MainKernelCancelled()
