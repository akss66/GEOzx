"""Scope and lifecycle helpers for knowledge available to agents."""

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import require_client_access, require_project_access
from app.models import (
    BrainTask,
    Deliverable,
    Event,
    KnowledgeCitation,
    KnowledgeEntry,
    KnowledgeSuggestion,
    Project,
    User,
)
from app.models.enums import KnowledgeCategory, WorkspaceRole

OPERATING_ROLES = {WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR}


async def require_knowledge_scope(
    session: AsyncSession,
    user: User,
    client_id: int,
    project_id: int | None,
    *,
    writable: bool,
):
    roles = OPERATING_ROLES if writable else None
    client = await require_client_access(session, user, client_id, roles=roles)
    project = None
    if project_id is not None:
        project = await require_project_access(session, user, project_id, roles=roles)
        if project.client_id != client.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="项目不属于当前客户",
            )
    return client, project


async def get_scoped_entry(
    session: AsyncSession,
    user: User,
    entry_id: int,
    *,
    writable: bool,
) -> KnowledgeEntry:
    entry = await session.get(KnowledgeEntry, entry_id)
    if entry is None or entry.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    await require_knowledge_scope(
        session,
        user,
        entry.client_id,
        entry.project_id,
        writable=writable,
    )
    return entry


async def list_scoped_knowledge(
    session: AsyncSession,
    user: User,
    *,
    client_id: int,
    project_id: int | None,
    category: KnowledgeCategory | None = None,
) -> list[KnowledgeEntry]:
    await require_knowledge_scope(session, user, client_id, project_id, writable=False)
    q = select(KnowledgeEntry).where(
        KnowledgeEntry.org_id == user.org_id,
        KnowledgeEntry.client_id == client_id,
        KnowledgeEntry.status == "active",
    )
    if project_id is None:
        q = q.where(KnowledgeEntry.project_id.is_(None))
    else:
        q = q.where(
            or_(KnowledgeEntry.project_id.is_(None), KnowledgeEntry.project_id == project_id)
        )
    if category is not None:
        q = q.where(KnowledgeEntry.category == category)
    return list(await session.scalars(q.order_by(KnowledgeEntry.updated_at.desc())))


async def list_agent_knowledge(
    session: AsyncSession,
    *,
    org_id: int,
    client_id: int,
    project_id: int,
    limit: int = 24,
) -> list[KnowledgeEntry]:
    return list(
        await session.scalars(
            select(KnowledgeEntry)
            .where(
                KnowledgeEntry.org_id == org_id,
                KnowledgeEntry.client_id == client_id,
                KnowledgeEntry.status == "active",
                or_(KnowledgeEntry.project_id.is_(None), KnowledgeEntry.project_id == project_id),
            )
            .order_by(KnowledgeEntry.updated_at.desc())
            .limit(limit)
        )
    )


def knowledge_context(rows: list[KnowledgeEntry]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row.category.value, []).append(
            {
                "id": row.id,
                "title": row.title,
                "content": row.content,
                "tags": row.tags or [],
                "source": row.source_label,
                "version": row.version,
            }
        )
    return grouped


async def record_knowledge_citations(
    session: AsyncSession,
    *,
    rows: list[KnowledgeEntry],
    org_id: int,
    client_id: int,
    project_id: int,
    task_id: int,
    invocation_id: int,
    agent_code: str,
    context: str,
) -> list[KnowledgeCitation]:
    citations = [
        KnowledgeCitation(
            org_id=org_id,
            client_id=client_id,
            project_id=project_id,
            entry_id=row.id,
            task_id=task_id,
            invocation_id=invocation_id,
            agent_code=agent_code,
            context=context,
        )
        for row in rows
    ]
    session.add_all(citations)
    if citations:
        await session.flush()
    return citations


async def validate_suggestion_sources(
    session: AsyncSession,
    user: User,
    *,
    task_id: int | None,
    deliverable_id: int | None,
) -> None:
    if task_id is not None:
        task = await session.get(BrainTask, task_id)
        if task is None or task.org_id != user.org_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="来源任务无效")
    if deliverable_id is not None:
        deliverable = await session.get(Deliverable, deliverable_id)
        if deliverable is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="来源成果无效")


async def get_scoped_suggestion(
    session: AsyncSession,
    user: User,
    suggestion_id: int,
    *,
    writable: bool,
) -> KnowledgeSuggestion:
    suggestion = await session.get(KnowledgeSuggestion, suggestion_id)
    if suggestion is None or suggestion.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识建议不存在")
    await require_knowledge_scope(
        session,
        user,
        suggestion.client_id,
        suggestion.project_id,
        writable=writable,
    )
    return suggestion


def knowledge_event(
    event_type: str,
    *,
    project_id: int | None,
    entry_id: int | None = None,
    suggestion_id: int | None = None,
    actor_user_id: int,
) -> Event:
    return Event(
        type=event_type,
        project_id=project_id,
        payload={
            "entry_id": entry_id,
            "suggestion_id": suggestion_id,
            "actor_user_id": actor_user_id,
            "occurred_at": datetime.now(UTC).isoformat(),
        },
    )


async def require_project_matches_client(
    session: AsyncSession, project_id: int | None, client_id: int
) -> Project | None:
    if project_id is None:
        return None
    project = await session.get(Project, project_id)
    if project is None or project.client_id != client_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="项目不属于当前客户")
    return project
