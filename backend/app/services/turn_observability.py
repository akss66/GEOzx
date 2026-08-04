"""Turn-scoped latency and provider-attempt telemetry.

The ContextVar carries only bounded ownership identifiers and timing state.
Provider payloads, prompts, credentials, and tool inputs never enter it.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConversationTurn


class TurnClock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


SYSTEM_TURN_CLOCK: TurnClock = _SystemClock()
METRICS_LOGGER = logging.getLogger("dyflow.turn_metrics")

_TURN_METRIC_DIMENSIONS = {
    "turn_event_publish_ms": frozenset({"event_type", "outcome"}),
    "turn_event_delivery_lag_ms": frozenset({"event_type", "transport"}),
    "turn_event_sequence_gap_total": frozenset({"event_type", "transport"}),
    "turn_event_duplicate_total": frozenset({"event_type", "transport"}),
    "turn_stream_reconnect_total": frozenset({"transport", "reason"}),
}
_PUBLIC_EVENT_TYPES = frozenset(
    {
        "turn.received",
        "turn.completed",
        "turn.failed",
        "turn.blocked",
        "turn.cancelled",
        "turn.stopped",
        "step.started",
        "step.completed",
        "step.failed",
        "deliverable.updated",
    }
)
_METRIC_DIMENSION_VALUES = {
    "event_type": _PUBLIC_EVENT_TYPES,
    "outcome": frozenset({"appended"}),
    "transport": frozenset({"durable", "sse"}),
    "reason": frozenset({"resume"}),
}


def emit_turn_metric(
    metric_name: str,
    metric_value: int | float,
    *,
    dimensions: Mapping[str, str],
) -> None:
    """Emit a bounded structured record without allowing telemetry to fail a Turn."""

    allowed_dimensions = _TURN_METRIC_DIMENSIONS.get(metric_name)
    if allowed_dimensions is None:
        return
    if not isinstance(metric_value, (int, float)) or not math.isfinite(metric_value):
        return
    safe_dimensions = {
        key: value
        for key, value in dimensions.items()
        if (
            key in allowed_dimensions
            and isinstance(value, str)
            and value in _METRIC_DIMENSION_VALUES[key]
        )
    }
    if set(safe_dimensions) != allowed_dimensions:
        return
    try:
        METRICS_LOGGER.info(
            "turn_metric",
            extra={
                "metric_name": metric_name,
                "metric_value": max(0, metric_value),
                "metric_dimensions": safe_dimensions,
            },
        )
    except Exception:  # noqa: BLE001 - observability cannot fail a user-visible stream
        return


def record_turn_event_publish(
    started_monotonic: float,
    *,
    event_type: str,
    outcome: str,
) -> None:
    emit_turn_metric(
        "turn_event_publish_ms",
        round(max(0.0, time.monotonic() - started_monotonic) * 1000),
        dimensions={"event_type": event_type, "outcome": outcome},
    )


def record_turn_event_delivery_lag(
    created_at: datetime,
    *,
    event_type: str,
) -> None:
    emit_turn_metric(
        "turn_event_delivery_lag_ms",
        _elapsed_since_created(created_at, datetime.now(UTC)),
        dimensions={"event_type": event_type, "transport": "sse"},
    )


def record_turn_event_sequence_gap(*, event_type: str) -> None:
    emit_turn_metric(
        "turn_event_sequence_gap_total",
        1,
        dimensions={"event_type": event_type, "transport": "sse"},
    )


def record_turn_event_duplicate(*, event_type: str) -> None:
    emit_turn_metric(
        "turn_event_duplicate_total",
        1,
        dimensions={"event_type": event_type, "transport": "durable"},
    )


def record_turn_stream_reconnect(*, after_id: int) -> None:
    if after_id <= 0:
        return
    emit_turn_metric(
        "turn_stream_reconnect_total",
        1,
        dimensions={"transport": "sse", "reason": "resume"},
    )


@dataclass
class TurnObservabilityScope:
    org_id: int
    thread_id: int
    turn_id: int
    run_id: int
    turn_created_at: datetime
    execution_started_monotonic: float | None = None
    clock: TurnClock = SYSTEM_TURN_CLOCK


_turn_scope: ContextVar[TurnObservabilityScope | None] = ContextVar(
    "turn_observability_scope",
    default=None,
)


@contextmanager
def bind_turn_observability(
    scope: TurnObservabilityScope,
    *,
    clock: TurnClock | None = None,
) -> Iterator[TurnObservabilityScope]:
    bound = TurnObservabilityScope(
        org_id=scope.org_id,
        thread_id=scope.thread_id,
        turn_id=scope.turn_id,
        run_id=scope.run_id,
        turn_created_at=scope.turn_created_at,
        execution_started_monotonic=scope.execution_started_monotonic,
        clock=clock or scope.clock,
    )
    token = _turn_scope.set(bound)
    try:
        yield bound
    finally:
        _turn_scope.reset(token)


def current_turn_observability() -> TurnObservabilityScope | None:
    return _turn_scope.get()


def mark_execution_started() -> None:
    scope = _turn_scope.get()
    if scope is not None and scope.execution_started_monotonic is None:
        scope.execution_started_monotonic = scope.clock.monotonic()


async def record_route_completed(session: AsyncSession) -> None:
    scope = _turn_scope.get()
    if scope is None or scope.execution_started_monotonic is None:
        return
    elapsed = max(
        0,
        round((scope.clock.monotonic() - scope.execution_started_monotonic) * 1000),
    )
    await session.execute(
        update(ConversationTurn)
        .where(
            ConversationTurn.id == scope.turn_id,
            ConversationTurn.thread_id == scope.thread_id,
            ConversationTurn.org_id == scope.org_id,
            ConversationTurn.route_ms.is_(None),
        )
        .values(route_ms=elapsed)
    )
    # Routing is useful even when a later provider/tool attempt is rolled back
    # before the Worker records retry_wait.
    await session.commit()


async def record_first_user_token(
    session: AsyncSession,
    *,
    agent_code: str,
    delta: str,
) -> None:
    scope = _turn_scope.get()
    if scope is None or agent_code != "00-decision" or not delta.strip():
        return
    elapsed = _elapsed_since_created(scope.turn_created_at, scope.clock.now())
    await session.execute(
        update(ConversationTurn)
        .where(
            ConversationTurn.id == scope.turn_id,
            ConversationTurn.thread_id == scope.thread_id,
            ConversationTurn.org_id == scope.org_id,
            ConversationTurn.first_token_ms.is_(None),
        )
        .values(first_token_ms=elapsed)
    )
    # TTFT must survive a provider fallback or a later retry rollback.
    await session.commit()


async def increment_model_call_count(session: AsyncSession) -> None:
    """Atomically count one real provider attempt in the active Turn."""

    scope = _turn_scope.get()
    if scope is None:
        return
    await session.execute(
        update(ConversationTurn)
        .where(
            ConversationTurn.id == scope.turn_id,
            ConversationTurn.thread_id == scope.thread_id,
            ConversationTurn.org_id == scope.org_id,
        )
        .values(
            model_call_count=func.coalesce(
                ConversationTurn.model_call_count,
                0,
            )
            + 1
        )
    )


async def increment_tool_call_count(session: AsyncSession) -> None:
    """Atomically count one terminal tool attempt in the active Turn."""

    scope = _turn_scope.get()
    if scope is None:
        return
    await session.execute(
        update(ConversationTurn)
        .where(
            ConversationTurn.id == scope.turn_id,
            ConversationTurn.thread_id == scope.thread_id,
            ConversationTurn.org_id == scope.org_id,
        )
        .values(
            tool_call_count=func.coalesce(
                ConversationTurn.tool_call_count,
                0,
            )
            + 1
        )
    )


def apply_turn_closure_metrics(
    turn: ConversationTurn,
    *,
    now: datetime | None = None,
    writes_user_message: bool,
) -> None:
    """Apply T4/T5 just before the runtime closure transaction commits."""

    elapsed = _elapsed_since_created(turn.created_at, now or datetime.now(UTC))
    if writes_user_message:
        turn.completion_ms = elapsed
    turn.total_ms = elapsed


def _elapsed_since_created(created_at: datetime, now: datetime) -> int:
    created = _as_utc(created_at)
    current = _as_utc(now)
    return max(0, round((current - created).total_seconds() * 1000))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
