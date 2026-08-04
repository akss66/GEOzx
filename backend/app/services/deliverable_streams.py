"""Canonical identity and query helpers for one deliverable version stream."""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Deliverable
from app.models.enums import AgentCode, DeliverableType


def deliverable_stream_clause(
    *,
    content_item_id: int,
    agent_code: str | AgentCode,
    deliverable_type: DeliverableType,
) -> ColumnElement[bool]:
    """Return the complete, explicit identity predicate for one version stream."""

    code = agent_code.value if isinstance(agent_code, AgentCode) else agent_code
    if not code:
        raise ValueError("agent_code is required for a deliverable stream")
    return and_(
        Deliverable.content_item_id == content_item_id,
        Deliverable.agent_code == code,
        Deliverable.type == deliverable_type,
    )


async def latest_deliverable_version(
    session: AsyncSession,
    *,
    content_item_id: int,
    agent_code: str | AgentCode,
    deliverable_type: DeliverableType,
) -> int:
    latest = await session.scalar(
        select(func.max(Deliverable.version)).where(
            deliverable_stream_clause(
                content_item_id=content_item_id,
                agent_code=agent_code,
                deliverable_type=deliverable_type,
            )
        )
    )
    return int(latest or 0)


__all__ = ["deliverable_stream_clause", "latest_deliverable_version"]
