"""Transactional append and retrieval service for public WorkTurn events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import TurnEventPayload, turn_event_idempotency_key
from app.models import Account, AgentRun, ConversationThread, ConversationTurn, Event, SkillRun

MAX_LIST_LIMIT = 500

_TURN_FIELDS = frozenset(
    {
        "client_message_id",
        "status",
        "turn_phase",
        "message",
        "summary",
        "error_code",
        "reason",
        "recovery_action",
        "metadata",
    }
)
_STEP_FIELDS = frozenset(
    {
        "step",
        "step_id",
        "step_key",
        "step_name",
        "title",
        "status",
        "turn_phase",
        "progress",
        "message",
        "summary",
        "error_code",
        "reason",
        "recovery_action",
        "metadata",
    }
)
_DELIVERABLE_FIELDS = frozenset(
    {
        "deliverable_id",
        "deliverable_type",
        "version",
        "status",
        "title",
        "summary",
        "turn_phase",
        "updated_fields",
        "metadata",
    }
)

_METADATA_FIELDS = frozenset(
    {
        "attempt",
        "cached",
        "category",
        "confidence",
        "data_freshness",
        "evidence_count",
        "kind",
        "label",
        "max_attempts",
        "phase",
        "retryable",
        "source",
        "source_id",
        "source_type",
        "status",
    }
)
_PROGRESS_FIELDS = frozenset(
    {
        "completed",
        "current",
        "details",
        "label",
        "message",
        "percent",
        "status",
        "total",
        "total_steps",
        "unit",
    }
)
_PROGRESS_DETAIL_FIELDS = frozenset(
    {"current", "label", "status", "summary", "total"}
)

PUBLIC_EVENT_PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "turn.received": _TURN_FIELDS,
    "turn.completed": _TURN_FIELDS,
    "turn.failed": _TURN_FIELDS,
    "turn.blocked": _TURN_FIELDS,
    "turn.cancelled": _TURN_FIELDS,
    "turn.stopped": _TURN_FIELDS,
    "step.started": _STEP_FIELDS,
    "step.completed": _STEP_FIELDS,
    "step.failed": _STEP_FIELDS,
    "deliverable.updated": _DELIVERABLE_FIELDS,
}
_TERMINAL_EVENT_TYPES = frozenset(
    {
        "turn.completed",
        "turn.failed",
        "turn.blocked",
        "turn.cancelled",
        "turn.stopped",
    }
)

@dataclass(frozen=True)
class TurnEventScope:
    org_id: int
    account_id: int
    thread_id: int
    turn_id: int
    run_id: int | None = None
    skill_run_id: int | None = None


@dataclass(frozen=True)
class ThreadEventScope:
    org_id: int
    account_id: int
    thread_id: int


async def append_turn_event(
    session: AsyncSession,
    scope: TurnEventScope,
    event_type: str,
    payload: Mapping[str, object],
    idempotency_key: str,
) -> Event:
    """Append one public Turn event without committing the caller's transaction."""

    public_payload = _public_payload(event_type, payload)
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError("turn event idempotency key must not be empty")
    if idempotency_key == "terminal" and event_type not in _TERMINAL_EVENT_TYPES:
        raise ValueError("turn event idempotency key 'terminal' is reserved for terminal events")

    allow_terminal_replay = (
        idempotency_key == "terminal" and event_type in _TERMINAL_EVENT_TYPES
    )
    turn = await _validate_scope(session, scope, lock_turn=True)
    key_scope = replace(scope, skill_run_id=None) if allow_terminal_replay else scope
    database_key = turn_event_idempotency_key(key_scope, idempotency_key)
    existing = await _find_by_idempotency_key(session, database_key)
    if existing is not None:
        _require_matching_event(
            existing,
            scope,
            event_type,
            allow_terminal_replay=allow_terminal_replay,
        )
        return existing

    # Python's sqlite3 legacy transaction mode does not start a physical
    # transaction for SELECT. Establish one before the SAVEPOINT so releasing
    # it cannot commit the event behind the caller's back. PostgreSQL's locked
    # SELECT already starts the real transaction and needs no compatibility DML.
    if session.get_bind().dialect.name == "sqlite":
        await session.execute(
            text(
                "UPDATE conversation_turns "
                "SET next_event_sequence = next_event_sequence WHERE id = :turn_id"
            ),
            {"turn_id": scope.turn_id},
        )
    try:
        async with session.begin_nested():
            sequence = turn.next_event_sequence
            turn.next_event_sequence = sequence + 1
            event = Event(
                type=event_type,
                org_id=scope.org_id,
                account_id=scope.account_id,
                thread_id=scope.thread_id,
                turn_id=scope.turn_id,
                run_id=scope.run_id,
                skill_run_id=scope.skill_run_id,
                sequence=sequence,
                payload=public_payload,
                idempotency_key=database_key,
            )
            session.add(event)
            await session.flush()
    except IntegrityError:
        existing = await _find_by_idempotency_key(session, database_key)
        if existing is None or not _event_matches(
            existing,
            scope,
            event_type,
            allow_terminal_replay=allow_terminal_replay,
        ):
            raise
        return existing
    return event


async def list_turn_events(
    session: AsyncSession,
    scope: TurnEventScope,
    *,
    after_id: int = 0,
    limit: int = MAX_LIST_LIMIT,
) -> list[Event]:
    """Return ordered events from exactly one authorized Turn scope."""

    if isinstance(after_id, bool) or not isinstance(after_id, int) or after_id < 0:
        raise ValueError("after_id must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIST_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIST_LIMIT}")

    await _validate_scope(session, scope, lock_turn=False)
    conditions = [
        Event.org_id == scope.org_id,
        Event.account_id == scope.account_id,
        Event.thread_id == scope.thread_id,
        Event.turn_id == scope.turn_id,
        Event.id > after_id,
    ]
    if scope.run_id is not None:
        conditions.append(Event.run_id == scope.run_id)
    if scope.skill_run_id is not None:
        conditions.append(Event.skill_run_id == scope.skill_run_id)
    return list(
        await session.scalars(
            select(Event)
            .where(*conditions)
            .order_by(Event.sequence.asc(), Event.id.asc())
            .limit(limit)
        )
    )


async def list_thread_events(
    session: AsyncSession,
    scope: ThreadEventScope,
    *,
    after_id: int = 0,
    limit: int = MAX_LIST_LIMIT,
) -> list[Event]:
    """Return one bounded database page from an already authorized Thread."""

    if isinstance(after_id, bool) or not isinstance(after_id, int) or after_id < 0:
        raise ValueError("after_id must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIST_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIST_LIMIT}")
    return list(
        await session.scalars(
            select(Event)
            .where(
                Event.org_id == scope.org_id,
                Event.account_id == scope.account_id,
                Event.thread_id == scope.thread_id,
                Event.id > after_id,
                Event.type.in_(PUBLIC_EVENT_PAYLOAD_FIELDS),
                Event.sequence > 0,
                Event.turn_id.is_not(None),
            )
            .order_by(Event.id.asc())
            .limit(limit)
        )
    )


async def _validate_scope(
    session: AsyncSession,
    scope: TurnEventScope,
    *,
    lock_turn: bool,
) -> ConversationTurn:
    statement = (
        select(ConversationTurn, ConversationThread, Account)
        .join(ConversationThread, ConversationThread.id == ConversationTurn.thread_id)
        .join(Account, Account.id == ConversationThread.account_id)
        .where(ConversationTurn.id == scope.turn_id)
    )
    if lock_turn:
        statement = statement.with_for_update(of=ConversationTurn).execution_options(
            populate_existing=True
        )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise ValueError("turn event scope does not match persisted records")
    turn, thread, account = row
    if (
        turn.thread_id != scope.thread_id
        or turn.org_id != scope.org_id
        or thread.id != scope.thread_id
        or thread.org_id != scope.org_id
        or thread.account_id != scope.account_id
        or account.id != scope.account_id
        or account.org_id != scope.org_id
    ):
        raise ValueError("turn event scope does not match persisted records")

    if scope.run_id is not None:
        run = await session.scalar(select(AgentRun).where(AgentRun.id == scope.run_id))
        if (
            run is None
            or run.org_id != scope.org_id
            or run.thread_id != scope.thread_id
            or run.turn_id != scope.turn_id
        ):
            raise ValueError("turn event run scope does not match persisted records")

    if scope.skill_run_id is not None:
        skill_run = await session.scalar(
            select(SkillRun).where(SkillRun.id == scope.skill_run_id)
        )
        if (
            skill_run is None
            or skill_run.org_id != scope.org_id
            or skill_run.thread_id != scope.thread_id
            or skill_run.turn_id != scope.turn_id
            or (scope.run_id is not None and skill_run.run_id != scope.run_id)
        ):
            raise ValueError("turn event skill run scope does not match persisted records")
    return turn


async def _find_by_idempotency_key(
    session: AsyncSession,
    idempotency_key: str,
) -> Event | None:
    return await session.scalar(
        select(Event).where(Event.idempotency_key == idempotency_key)
    )


def _require_matching_event(
    event: Event,
    scope: TurnEventScope,
    event_type: str,
    *,
    allow_terminal_replay: bool,
) -> None:
    if not _event_matches(
        event,
        scope,
        event_type,
        allow_terminal_replay=allow_terminal_replay,
    ):
        raise ValueError("turn event idempotency key conflicts with another event")


def _event_matches(
    event: Event,
    scope: TurnEventScope,
    event_type: str,
    *,
    allow_terminal_replay: bool,
) -> bool:
    return (
        (
            event.type == event_type
            or (
                allow_terminal_replay
                and
                event.type in _TERMINAL_EVENT_TYPES
                and event_type in _TERMINAL_EVENT_TYPES
            )
        )
        and event.org_id == scope.org_id
        and event.account_id == scope.account_id
        and event.thread_id == scope.thread_id
        and event.turn_id == scope.turn_id
        and event.run_id == scope.run_id
        and (
            allow_terminal_replay
            or event.skill_run_id == scope.skill_run_id
        )
    )


def _public_payload(
    event_type: str,
    payload: Mapping[str, object],
) -> TurnEventPayload:
    allowed_fields = PUBLIC_EVENT_PAYLOAD_FIELDS.get(event_type)
    if allowed_fields is None:
        raise ValueError(f"unsupported public turn event type: {event_type}")
    if not isinstance(payload, Mapping):
        raise TypeError("turn event payload must be a mapping")
    return {
        key: _sanitize_top_level_value(key, value)
        for key, value in payload.items()
        if isinstance(key, str) and key in allowed_fields
    }


def public_turn_event_payload(
    event_type: str,
    payload: Mapping[str, object],
) -> TurnEventPayload:
    """Sanitize a persisted payload again at the public read boundary."""

    allowed_fields = PUBLIC_EVENT_PAYLOAD_FIELDS.get(event_type)
    if allowed_fields is None:
        return {}
    sanitized: TurnEventPayload = {}
    for key, value in payload.items():
        if not isinstance(key, str) or key not in allowed_fields:
            continue
        try:
            sanitized[key] = _sanitize_top_level_value(key, value)
        except (TypeError, ValueError):
            continue
    return sanitized


def _sanitize_top_level_value(key: str, value: object) -> object:
    if key == "metadata":
        return _sanitize_public_mapping(value, _METADATA_FIELDS)
    if key == "progress":
        if value is None or isinstance(value, int | float):
            return value
        return _sanitize_progress(value)
    if key == "updated_fields":
        if value is None:
            return None
        if not isinstance(value, list | tuple) or not all(
            isinstance(item, str) for item in value
        ):
            raise TypeError("turn event updated_fields must be a list of strings")
        return list(value)
    return _sanitize_scalar(value)


def _sanitize_progress(value: object) -> TurnEventPayload:
    sanitized = _sanitize_public_mapping(value, _PROGRESS_FIELDS, skip_fields={"details"})
    if isinstance(value, Mapping) and "details" in value:
        details = value["details"]
        if not isinstance(details, list | tuple):
            raise TypeError("turn event progress details must be a list")
        sanitized["details"] = [
            _sanitize_public_mapping(item, _PROGRESS_DETAIL_FIELDS) for item in details
        ]
    return sanitized


def _sanitize_public_mapping(
    value: object,
    allowed_fields: frozenset[str],
    *,
    skip_fields: set[str] | None = None,
) -> TurnEventPayload:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("turn event structured payload field must be a mapping")
    skipped = skip_fields or set()
    return {
        key: _sanitize_scalar(item)
        for key, item in value.items()
        if isinstance(key, str) and key in allowed_fields and key not in skipped
    }


def _sanitize_scalar(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported turn event payload value: {type(value).__name__}")
