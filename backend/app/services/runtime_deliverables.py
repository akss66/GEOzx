"""Single audited write boundary for formal runtime deliverables."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContentItem, Deliverable
from app.models.enums import DeliverableStatus, DeliverableType
from app.orchestrator.runtime_scope import RuntimeScope, RuntimeScopeConflict
from app.services.deliverable_streams import (
    deliverable_stream_clause,
    latest_deliverable_version,
)
from app.services.turn_events import TurnEventScope, append_turn_event

_PROVENANCE_FIELDS = ("thread_id", "turn_id", "run_id", "skill_run_id")


async def write_runtime_deliverable(
    session: AsyncSession,
    *,
    scope: RuntimeScope | None,
    content: ContentItem,
    agent_code: str,
    deliverable_type: DeliverableType,
    version: int | None = None,
    status: DeliverableStatus,
    payload: dict[str, Any],
    note: str | None = None,
    legacy_provenance: dict[str, int | None] | None = None,
) -> Deliverable:
    """Validate provenance before adding one formal Deliverable.

    Legacy callers may omit ``scope`` only when every provenance field is null.
    """

    # Compatibility-only input for older callers outside this integration
    # slice. Allocation below is authoritative and never trusts this value.
    del version
    provenance = {field: (legacy_provenance or {}).get(field) for field in _PROVENANCE_FIELDS}
    if scope is None:
        if any(value is not None for value in provenance.values()):
            raise RuntimeScopeConflict("partial legacy provenance is not allowed")
    else:
        if legacy_provenance is not None:
            raise RuntimeScopeConflict("runtime provenance must come from RuntimeScope")
        await scope.validate(session)
        if content.account_id != scope.account_id:
            raise RuntimeScopeConflict("deliverable content account does not match")
        provenance = {
            "thread_id": scope.thread_id,
            "turn_id": scope.turn_id,
            "run_id": scope.run_id,
            "skill_run_id": scope.skill_run_id,
        }

    if content.id is None:
        raise RuntimeScopeConflict("deliverable content must be persisted")
    await session.scalar(
        select(ContentItem.id).where(ContentItem.id == content.id).with_for_update()
    )
    if scope is not None and scope.skill_run_id is not None:
        replay = await session.scalar(
            select(Deliverable)
            .where(
                deliverable_stream_clause(
                    content_item_id=content.id,
                    agent_code=agent_code,
                    deliverable_type=deliverable_type,
                ),
                Deliverable.skill_run_id == scope.skill_run_id,
            )
            .order_by(Deliverable.id)
            .limit(1)
            .with_for_update()
        )
        if replay is not None:
            if (
                replay.status == status
                and replay.payload == payload
                and replay.note == note
                and all(getattr(replay, field) == provenance[field] for field in _PROVENANCE_FIELDS)
            ):
                return replay
            raise RuntimeScopeConflict("runtime deliverable replay differs from durable write")

    version = await latest_deliverable_version(
        session,
        content_item_id=content.id,
        agent_code=agent_code,
        deliverable_type=deliverable_type,
    ) + 1

    deliverable = Deliverable(
        content_item_id=content.id,
        agent_code=agent_code,
        type=deliverable_type,
        version=version,
        status=status,
        payload=payload,
        note=note,
        **provenance,
    )
    session.add(deliverable)
    await session.flush()
    if scope is not None:
        await append_turn_event(
            session,
            TurnEventScope(
                org_id=scope.org_id,
                account_id=scope.account_id,
                thread_id=scope.thread_id,
                turn_id=scope.turn_id,
                run_id=scope.run_id,
                skill_run_id=scope.skill_run_id,
            ),
            "deliverable.updated",
            {
                "deliverable_id": deliverable.id,
                "deliverable_type": deliverable.type.value,
                "version": deliverable.version,
                "status": deliverable.status.value,
            },
            f"deliverable:{deliverable.id}:v{deliverable.version}",
        )
    return deliverable
