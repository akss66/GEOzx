"""Bounded, tenant-scoped semantic memory projection for the AI COO."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    Client,
    ExperienceMemory,
    KnowledgeEntry,
    Org,
    PlatformContentRecord,
    Project,
)
from app.schemas.ai_coo import (
    AccountMemoryLayer,
    BusinessMemoryLayer,
    ContentMemoryLayer,
    COOMemoryContext,
    ExperienceMemoryItem,
    ExperienceMemoryLayer,
)

_MEMORY_LIMIT = 12


async def build_coo_memory_context(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int | None,
    client_ids: list[int],
    project_ids: list[int],
    situation_summary: dict[str, Any],
) -> COOMemoryContext:
    """Load four auditable memory layers without crossing runtime scope."""

    org_name = (
        await session.scalar(select(Org.name).where(Org.id == org_id))
    ) or ""
    clients = await _load_clients(session, org_id=org_id, client_ids=client_ids)
    projects = await _load_projects(
        session,
        org_id=org_id,
        project_ids=project_ids,
    )
    reviewed_knowledge = await _load_reviewed_knowledge(
        session,
        org_id=org_id,
        client_ids=client_ids,
        project_ids=project_ids,
    )
    account = await _load_account(
        session,
        org_id=org_id,
        account_id=account_id,
        situation_summary=situation_summary,
    )
    content = await _load_recent_content(
        session,
        org_id=org_id,
        account_id=account_id,
    )
    experience = await _load_verified_experience(
        session,
        org_id=org_id,
        account_id=account_id,
        client_ids=client_ids,
        project_ids=project_ids,
    )
    return COOMemoryContext(
        business=BusinessMemoryLayer(
            org_id=org_id,
            org_name=org_name,
            clients=clients,
            projects=projects,
            reviewed_knowledge=reviewed_knowledge,
        ),
        account=account,
        content=ContentMemoryLayer(recent_items=content),
        experience=ExperienceMemoryLayer(items=experience),
    )


async def _load_clients(
    session: AsyncSession,
    *,
    org_id: int,
    client_ids: list[int],
) -> list[dict[str, Any]]:
    if not client_ids:
        return []
    rows = (
        await session.execute(
            select(Client.id, Client.name)
            .where(Client.org_id == org_id, Client.id.in_(client_ids))
            .order_by(Client.id)
            .limit(20)
        )
    ).all()
    return [{"id": row.id, "name": row.name} for row in rows]


async def _load_projects(
    session: AsyncSession,
    *,
    org_id: int,
    project_ids: list[int],
) -> list[dict[str, Any]]:
    if not project_ids:
        return []
    rows = (
        await session.execute(
            select(
                Project.id,
                Project.client_id,
                Project.name,
                Project.description,
            )
            .where(Project.org_id == org_id, Project.id.in_(project_ids))
            .order_by(Project.id)
            .limit(20)
        )
    ).all()
    return [
        {
            "id": row.id,
            "client_id": row.client_id,
            "name": row.name,
            "description": row.description or "",
        }
        for row in rows
    ]


async def _load_reviewed_knowledge(
    session: AsyncSession,
    *,
    org_id: int,
    client_ids: list[int],
    project_ids: list[int],
) -> list[dict[str, Any]]:
    if not client_ids:
        return []
    project_scope = (
        or_(
            KnowledgeEntry.project_id.is_(None),
            KnowledgeEntry.project_id.in_(project_ids),
        )
        if project_ids
        else KnowledgeEntry.project_id.is_(None)
    )
    entries = (
        await session.scalars(
            select(KnowledgeEntry)
            .where(
                KnowledgeEntry.org_id == org_id,
                KnowledgeEntry.client_id.in_(client_ids),
                KnowledgeEntry.status == "active",
                project_scope,
            )
            .order_by(KnowledgeEntry.updated_at.desc(), KnowledgeEntry.id.desc())
            .limit(_MEMORY_LIMIT)
        )
    ).all()
    return [
        {
            "id": item.id,
            "client_id": item.client_id,
            "project_id": item.project_id,
            "category": item.category.value,
            "title": item.title,
            "content_excerpt": item.content[:500],
            "source_label": item.source_label,
            "version": item.version,
        }
        for item in entries
    ]


async def _load_account(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int | None,
    situation_summary: dict[str, Any],
) -> AccountMemoryLayer:
    if account_id is None:
        return AccountMemoryLayer(situation_summary=situation_summary)
    account = await session.scalar(
        select(Account).where(Account.org_id == org_id, Account.id == account_id)
    )
    if account is None:
        return AccountMemoryLayer(situation_summary=situation_summary)
    return AccountMemoryLayer(
        account_id=account.id,
        nickname=account.nickname,
        platform=account.platform.value,
        external_account_id=account.external_account_id,
        situation_summary=situation_summary,
    )


async def _load_recent_content(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int | None,
) -> list[dict[str, Any]]:
    if account_id is None:
        return []
    records = (
        await session.scalars(
            select(PlatformContentRecord)
            .where(
                PlatformContentRecord.org_id == org_id,
                PlatformContentRecord.account_id == account_id,
            )
            .order_by(
                PlatformContentRecord.published_at.desc(),
                PlatformContentRecord.id.desc(),
            )
            .limit(_MEMORY_LIMIT)
        )
    ).all()
    return [
        {
            "id": item.id,
            "external_content_id": item.external_content_id,
            "title": item.title or "",
            "published_at": (
                item.published_at.isoformat() if item.published_at else None
            ),
            "content_format": item.content_format or "",
            "review_status": item.review_status or "",
            "source_kind": item.source_kind.value,
        }
        for item in records
    ]


async def _load_verified_experience(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int | None,
    client_ids: list[int],
    project_ids: list[int],
) -> list[ExperienceMemoryItem]:
    scope_predicates = [
        and_(
            ExperienceMemory.account_id.is_(None),
            ExperienceMemory.client_id.is_(None),
            ExperienceMemory.project_id.is_(None),
        )
    ]
    if account_id is not None:
        scope_predicates.append(ExperienceMemory.account_id == account_id)
    if project_ids:
        scope_predicates.append(
            and_(
                ExperienceMemory.account_id.is_(None),
                ExperienceMemory.project_id.in_(project_ids),
            )
        )
    if client_ids:
        scope_predicates.append(
            and_(
                ExperienceMemory.account_id.is_(None),
                ExperienceMemory.project_id.is_(None),
                ExperienceMemory.client_id.in_(client_ids),
            )
        )
    memories = (
        await session.scalars(
            select(ExperienceMemory)
            .where(
                ExperienceMemory.org_id == org_id,
                ExperienceMemory.status == "verified",
                or_(*scope_predicates),
            )
            .order_by(
                ExperienceMemory.verified_at.desc(),
                ExperienceMemory.id.desc(),
            )
            .limit(_MEMORY_LIMIT)
        )
    ).all()
    return [
        ExperienceMemoryItem(
            id=item.id,
            industry=item.industry,
            action=item.action,
            condition=item.condition,
            result=item.result,
            confidence=item.confidence,
            source_refs=list(item.source_refs),
        )
        for item in memories
        if item.source_refs
    ]
