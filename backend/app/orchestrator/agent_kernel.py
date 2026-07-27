"""Shared control contracts for the main Agent and bounded specialists.

The kernel policy is a code boundary. Prompts may describe these rules, but
model output is never trusted to enforce them.
"""

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.schemas.brain import RuntimeToolCall
    from app.schemas.deliverable import DeliverablePayload


class KernelActor(StrEnum):
    MAIN = "main"
    SPECIALIST = "specialist"


class KernelAction(StrEnum):
    RESPOND = "respond"
    ASK_USER = "ask_user"
    DISPATCH_EXPERTS = "dispatch_experts"
    CALL_TOOLS = "call_tools"
    REQUEST_DECISION = "request_decision"
    REQUEST_PERMISSION = "request_permission"
    BLOCKED = "blocked"
    FINISH = "finish"


class KernelEventType(StrEnum):
    AGENT_START = "agent_start"
    TURN_START = "turn_start"
    DECISION = "decision"
    TOOL_START = "tool_start"
    TOOL_UPDATE = "tool_update"
    TOOL_END = "tool_end"
    TURN_END = "turn_end"
    AGENT_END = "agent_end"


class AgentKernelPolicyError(RuntimeError):
    """Raised when a model requests an action outside its runtime policy."""


@dataclass(frozen=True)
class SpecialistKernelDecision:
    action: KernelAction
    rationale: str
    tool_calls: tuple["RuntimeToolCall", ...] = ()
    deliverable: "DeliverablePayload | None" = None
    blocked_reason: str | None = None


@dataclass(frozen=True)
class AgentKernelPolicy:
    actor: KernelActor
    allowed_actions: frozenset[KernelAction]
    tool_allowlist: frozenset[str] | None
    max_rounds: int
    max_tool_calls: int

    def authorize(
        self,
        action: KernelAction | str,
        *,
        expert_codes: Collection[str] = (),
        tool_codes: Collection[str] = (),
    ) -> None:
        requested = KernelAction(action)
        if requested == KernelAction.DISPATCH_EXPERTS and self.actor != KernelActor.MAIN:
            raise AgentKernelPolicyError("specialist cannot dispatch specialists")
        if requested not in self.allowed_actions:
            raise AgentKernelPolicyError(
                f"action is not allowed for {self.actor.value}: {requested.value}"
            )
        if requested == KernelAction.DISPATCH_EXPERTS and not expert_codes:
            raise AgentKernelPolicyError("specialist dispatch requires at least one expert")
        if requested == KernelAction.CALL_TOOLS:
            if not tool_codes:
                raise AgentKernelPolicyError("tool action requires at least one tool")
            if self.tool_allowlist is not None:
                denied = sorted(set(tool_codes) - self.tool_allowlist)
                if denied:
                    raise AgentKernelPolicyError(
                        f"tool is not allowlisted for {self.actor.value}: {', '.join(denied)}"
                    )

    def assert_budget(self, *, round_index: int, tool_call_count: int) -> None:
        if round_index > self.max_rounds:
            raise AgentKernelPolicyError("round budget exhausted")
        if tool_call_count > self.max_tool_calls:
            raise AgentKernelPolicyError("tool budget exhausted")

    def as_context(self) -> dict[str, Any]:
        return {
            "actor": self.actor.value,
            "allowed_actions": sorted(action.value for action in self.allowed_actions),
            "tool_allowlist": (
                sorted(self.tool_allowlist) if self.tool_allowlist is not None else None
            ),
            "max_rounds": self.max_rounds,
            "max_tool_calls": self.max_tool_calls,
        }


def main_kernel_policy(
    *,
    max_rounds: int = 8,
    max_tool_calls: int = 12,
) -> AgentKernelPolicy:
    return AgentKernelPolicy(
        actor=KernelActor.MAIN,
        allowed_actions=frozenset(KernelAction),
        tool_allowlist=None,
        max_rounds=max_rounds,
        max_tool_calls=max_tool_calls,
    )


def expert_kernel_policy(
    *,
    tool_allowlist: Collection[str],
    max_rounds: int = 4,
    max_tool_calls: int = 6,
) -> AgentKernelPolicy:
    return AgentKernelPolicy(
        actor=KernelActor.SPECIALIST,
        allowed_actions=frozenset(
            {
                KernelAction.RESPOND,
                KernelAction.CALL_TOOLS,
                KernelAction.BLOCKED,
                KernelAction.FINISH,
            }
        ),
        tool_allowlist=frozenset(tool_allowlist),
        max_rounds=max_rounds,
        max_tool_calls=max_tool_calls,
    )
