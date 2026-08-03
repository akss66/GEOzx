"""Public, stable execution phases for the operator-facing conversation UI."""

from typing import Literal, cast

TurnPhase = Literal[
    "understanding",
    "reading_data",
    "consulting_experts",
    "quality_review",
    "waiting_approval",
    "composing_artifact",
    "completed",
    "failed",
]

TURN_PHASES = frozenset(
    {
        "understanding",
        "reading_data",
        "consulting_experts",
        "quality_review",
        "waiting_approval",
        "composing_artifact",
        "completed",
        "failed",
    }
)

_EVENT_PHASES: tuple[tuple[frozenset[str], TurnPhase], ...] = (
    (
        frozenset(
            {
                "brain.runtime.started",
                "brain.runtime.goal_understood",
                "brain.runtime.intent_classified",
                "brain.runtime.context_resolved",
                "brain.runtime.resumed",
            }
        ),
        "understanding",
    ),
    (
        frozenset(
            {
                "brain.runtime.context_loaded",
                "brain.runtime.tool_started",
                "brain.runtime.tool_completed",
            }
        ),
        "reading_data",
    ),
    (
        frozenset(
            {
                "brain.runtime.subagent_started",
                "brain.runtime.subagent_completed",
                "brain.runtime.handoff",
                "brain.runtime.task_planned",
                "brain.runtime.strategy_planned",
            }
        ),
        "consulting_experts",
    ),
    (
        frozenset(
            {
                "brain.runtime.critic_scored",
                "brain.runtime.critic_unavailable",
                "brain.runtime.reflection_completed",
                "brain.runtime.improvement_requested",
            }
        ),
        "quality_review",
    ),
    (
        frozenset(
            {
                "brain.runtime.approval_required",
                "brain.runtime.decision_requested",
                "brain.runtime.permission_request",
                "brain.runtime.clarification_requested",
                "brain.runtime.turn_paused",
            }
        ),
        "waiting_approval",
    ),
    (
        frozenset(
            {
                "brain.runtime.message_start",
                "brain.runtime.message_delta",
                "brain.runtime.message_done",
                "brain.runtime.next_strategy_ready",
            }
        ),
        "composing_artifact",
    ),
    (
        frozenset({"brain.runtime.completed"}),
        "completed",
    ),
    (
        frozenset(
            {
                "brain.runtime.failed",
                "brain.runtime.message_error",
                "brain.runtime.subagent_failed",
                "brain.runtime.policy_denied",
                "brain.runtime.generation_stopped",
                "brain.runtime.cancelled",
            }
        ),
        "failed",
    ),
)


def normalize_runtime_phase(
    event_type: str,
    payload: dict[str, object] | None,
) -> TurnPhase | None:
    """Return only a documented public phase; internal graph phases never leak."""

    explicit = (payload or {}).get("turn_phase")
    if isinstance(explicit, str) and explicit in TURN_PHASES:
        return cast(TurnPhase, explicit)
    for event_types, phase in _EVENT_PHASES:
        if event_type in event_types:
            return phase
    return None


def with_runtime_phase(
    event_type: str,
    payload: dict[str, object] | None,
) -> dict[str, object] | None:
    if not event_type.startswith("brain.runtime."):
        return payload
    phase = normalize_runtime_phase(event_type, payload)
    if phase is None:
        return payload
    return {**(payload or {}), "turn_phase": phase}
