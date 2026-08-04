"""Authenticated incremental recovery and SSE routes for conversation events."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.events import EVENTS_CHANNEL
from app.db import async_session, get_redis, get_session
from app.models import Event
from app.schemas.turn_events import (
    ConversationTurnEventListOut,
    ConversationTurnEventOut,
)
from app.services.conversations import get_conversation_thread
from app.services.turn_events import (
    MAX_LIST_LIMIT,
    PUBLIC_EVENT_PAYLOAD_FIELDS,
    ThreadEventScope,
    list_thread_events,
    public_turn_event_payload,
)
from app.services.turn_observability import (
    record_turn_event_delivery_lag,
    record_turn_event_sequence_gap,
    record_turn_stream_reconnect,
)

router = APIRouter(prefix="/conversation-threads", tags=["conversation-turn-events"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AfterId = Annotated[int, Query(ge=0)]
SSE_POLL_SECONDS = 1.0
SSE_HEARTBEAT_SECONDS = 15.0
event_session_factory = async_session


@dataclass(frozen=True)
class _StreamEvent:
    org_id: int
    account_id: int
    thread_id: int
    event: ConversationTurnEventOut


def _event_out(event) -> ConversationTurnEventOut:
    payload = event.payload if isinstance(event.payload, Mapping) else {}
    return ConversationTurnEventOut(
        id=event.id,
        sequence=event.sequence,
        type=event.type,
        payload=public_turn_event_payload(event.type, payload),
        thread_id=event.thread_id,
        turn_id=event.turn_id,
        run_id=event.run_id,
        skill_run_id=event.skill_run_id,
        created_at=event.created_at,
    )


def _event_belongs_to_scope(event, scope: ThreadEventScope) -> bool:
    return (
        event.org_id == scope.org_id
        and event.account_id == scope.account_id
        and event.thread_id == scope.thread_id
    )


def _sse_event_frame(event) -> str:
    output = event.event
    data = json.dumps(
        output.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"id: {output.id}\nevent: {output.type}\ndata: {data}\n\n"


async def _load_thread_event_page(
    session_factory: Callable,
    *,
    scope: ThreadEventScope,
    after_id: int,
) -> list:
    async with session_factory() as session:
        events = await list_thread_events(
            session,
            scope,
            after_id=after_id,
            limit=MAX_LIST_LIMIT,
        )
        return [
            _StreamEvent(
                org_id=event.org_id,
                account_id=event.account_id,
                thread_id=event.thread_id,
                event=_event_out(event),
            )
            for event in events
        ]


async def _load_turn_sequence_seed(
    session_factory: Callable,
    *,
    scope: ThreadEventScope,
    after_id: int,
) -> dict[int, int]:
    if after_id <= 0:
        return {}
    async with session_factory() as session:
        latest_ids = (
            select(
                Event.turn_id.label("turn_id"),
                func.max(Event.id).label("event_id"),
            )
            .where(
                Event.org_id == scope.org_id,
                Event.account_id == scope.account_id,
                Event.thread_id == scope.thread_id,
                Event.id <= after_id,
                Event.type.in_(PUBLIC_EVENT_PAYLOAD_FIELDS),
                Event.sequence > 0,
                Event.turn_id.is_not(None),
            )
            .group_by(Event.turn_id)
            .subquery()
        )
        rows = await session.execute(
            select(Event.turn_id, Event.sequence)
            .join(latest_ids, latest_ids.c.event_id == Event.id)
        )
        return {
            int(turn_id): int(sequence)
            for turn_id, sequence in rows
            if turn_id is not None and sequence is not None
        }


async def _close_pubsub(pubsub) -> None:
    try:
        await pubsub.unsubscribe(EVENTS_CHANNEL)
    except Exception:  # noqa: BLE001 - Redis is only an optional wake-up accelerator
        pass
    try:
        await pubsub.aclose()
    except Exception:  # noqa: BLE001 - cleanup must not replace stream termination
        pass


async def stream_authorized_thread_events(
    *,
    scope: ThreadEventScope,
    after_id: int,
    request: Request,
    session_factory: Callable | None = None,
    redis_client=None,
    poll_seconds: float = SSE_POLL_SECONDS,
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    """Stream DB-backed events; Redis only accelerates the next bounded poll."""

    factory = session_factory or event_session_factory
    redis = redis_client or get_redis()
    pubsub = None
    cursor = after_id
    last_heartbeat = monotonic()
    candidate_pubsub = None
    seen_sequences = await _load_turn_sequence_seed(
        factory,
        scope=scope,
        after_id=after_id,
    )
    reported_gaps: set[tuple[int, int, int]] = set()
    record_turn_stream_reconnect(after_id=after_id)
    try:
        candidate_pubsub = redis.pubsub()
        await candidate_pubsub.subscribe(EVENTS_CHANNEL)
    except Exception:  # noqa: BLE001 - every Redis failure degrades to DB polling
        if candidate_pubsub is not None:
            try:
                await candidate_pubsub.aclose()
            except Exception:  # noqa: BLE001 - best-effort unestablished cleanup
                pass
    else:
        pubsub = candidate_pubsub
    try:
        while True:
            if await request.is_disconnected():
                return
            events = await _load_thread_event_page(
                factory,
                scope=scope,
                after_id=cursor,
            )
            page_high_watermark = cursor
            for event in events:
                page_high_watermark = max(page_high_watermark, event.event.id)
                if not _event_belongs_to_scope(event, scope):
                    continue
                if await request.is_disconnected():
                    return
                cursor = event.event.id
                previous_sequence = seen_sequences.get(event.event.turn_id)
                if (
                    previous_sequence is not None
                    and event.event.sequence > previous_sequence + 1
                ):
                    gap = (
                        event.event.turn_id,
                        previous_sequence,
                        event.event.sequence,
                    )
                    if gap not in reported_gaps:
                        reported_gaps.add(gap)
                        record_turn_event_sequence_gap(event_type=event.event.type)
                if (
                    previous_sequence is None
                    or event.event.sequence >= previous_sequence
                ):
                    seen_sequences[event.event.turn_id] = event.event.sequence
                record_turn_event_delivery_lag(
                    event.event.created_at,
                    event_type=event.event.type,
                )
                yield _sse_event_frame(event)
            cursor = max(cursor, page_high_watermark)
            if len(events) == MAX_LIST_LIMIT:
                continue

            now = monotonic()
            heartbeat_remaining = max(0.0, heartbeat_seconds - (now - last_heartbeat))
            wait_seconds = min(poll_seconds, heartbeat_remaining)
            if pubsub is None:
                await asyncio.sleep(wait_seconds)
            else:
                try:
                    await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=wait_seconds,
                    )
                except Exception:  # noqa: BLE001 - every Redis failure degrades to DB polling
                    await _close_pubsub(pubsub)
                    pubsub = None
                    await asyncio.sleep(wait_seconds)
            now = monotonic()
            if now - last_heartbeat >= heartbeat_seconds:
                if await request.is_disconnected():
                    return
                yield ": heartbeat\n\n"
                last_heartbeat = now
    finally:
        if pubsub is not None:
            await _close_pubsub(pubsub)


@router.get("/{thread_id}/events", response_model=ConversationTurnEventListOut)
async def list_conversation_turn_events(
    thread_id: int,
    user: CurrentUser,
    session: SessionDep,
    after_id: AfterId = 0,
) -> ConversationTurnEventListOut:
    thread = await get_conversation_thread(session, user, thread_id)
    events = await list_thread_events(
        session,
        ThreadEventScope(
            org_id=user.org_id,
            account_id=thread.account_id,
            thread_id=thread.id,
        ),
        after_id=after_id,
        limit=MAX_LIST_LIMIT,
    )
    return ConversationTurnEventListOut(data=[_event_out(event) for event in events])


@router.get("/{thread_id}/event-stream")
async def stream_conversation_turn_events(
    thread_id: int,
    user: CurrentUser,
    session: SessionDep,
    request: Request,
    after_id: AfterId = 0,
) -> StreamingResponse:
    thread = await get_conversation_thread(session, user, thread_id)
    scope = ThreadEventScope(
        org_id=user.org_id,
        account_id=thread.account_id,
        thread_id=thread.id,
    )
    await session.rollback()
    await session.close()
    return StreamingResponse(
        stream_authorized_thread_events(
            scope=scope,
            after_id=after_id,
            request=request,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
