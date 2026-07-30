"""Single audited write boundary for formal runtime deliverables."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContentItem, Deliverable
from app.models.enums import DeliverableStatus, DeliverableType
from app.orchestrator.runtime_scope import RuntimeScope, RuntimeScopeConflict

_PROVENANCE_FIELDS = ("thread_id", "turn_id", "run_id", "skill_run_id")


async def write_runtime_deliverable(
    session: AsyncSession,
    *,
    scope: RuntimeScope | None,
    content: ContentItem,
    agent_code: str,
    deliverable_type: DeliverableType,
    version: int,
    status: DeliverableStatus,
    payload: dict[str, Any],
    note: str | None = None,
    legacy_provenance: dict[str, int | None] | None = None,
) -> Deliverable:
    """Validate provenance before adding one formal Deliverable.

    Legacy callers may omit ``scope`` only when every provenance field is null.
    """

    provenance = {
        field: (legacy_provenance or {}).get(field) for field in _PROVENANCE_FIELDS
    }
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
    return deliverable
