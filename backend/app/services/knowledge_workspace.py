"""Scope and lifecycle helpers for knowledge available to agents."""

from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.workspace_access import (
    accessible_client_ids,
    require_account_access,
    require_client_access,
    require_project_access,
)
from app.models import (
    Account,
    AccountKnowledgeBinding,
    BrainTask,
    Deliverable,
    Event,
    KnowledgeBase,
    KnowledgeCitation,
    KnowledgeEntry,
    KnowledgeSuggestion,
    Project,
    User,
)
from app.models.enums import KnowledgeCategory, UserRole, WorkspaceRole

OPERATING_ROLES = {WorkspaceRole.LEAD, WorkspaceRole.OPERATOR, WorkspaceRole.EDITOR}
FACT_REVIEW_ROLES = {WorkspaceRole.LEAD, WorkspaceRole.REVIEWER}
BINDING_ROLES = {WorkspaceRole.LEAD}
_MAX_AGENT_KNOWLEDGE_LIMIT = 24
_EXTERNAL_CLAIM_KINDS = {"product_fact", "case", "promise", "price", "numeric_claim"}


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


async def require_account_knowledge_scope(
    session: AsyncSession, user: User, account_id: int, *, writable: bool
) -> Account:
    """Load an authorized account before resolving its brand knowledge.

    Binding writers need a lead workspace role (or organization administrator);
    reads retain the existing account-visibility semantics.
    """

    return await require_account_access(
        session, user, account_id, roles=BINDING_ROLES if writable else None
    )


async def get_scoped_knowledge_base(
    session: AsyncSession,
    user: User,
    knowledge_base_id: int,
    *,
    writable: bool,
) -> KnowledgeBase:
    """Return a knowledge base only after its organization and client boundary match."""

    base = await session.get(KnowledgeBase, knowledge_base_id)
    if base is None or base.org_id != user.org_id or base.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    if base.client_id is None:
        if user.role != UserRole.ADMIN:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
        return base
    await require_client_access(
        session, user, base.client_id, roles=OPERATING_ROLES if writable else None
    )
    return base


async def list_scoped_knowledge_bases(
    session: AsyncSession, user: User, *, limit: int, offset: int
) -> tuple[list[KnowledgeBase], int]:
    """Page only bases visible through the caller's organization/client access."""

    query = select(KnowledgeBase).where(
        KnowledgeBase.org_id == user.org_id, KnowledgeBase.status == "active"
    )
    if user.role != UserRole.ADMIN:
        visible_client_ids = await accessible_client_ids(session, user)
        query = query.where(KnowledgeBase.client_id.in_(visible_client_ids))
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(
        await session.scalars(
            query.order_by(KnowledgeBase.updated_at.desc(), KnowledgeBase.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total or 0


async def get_account_primary_binding(
    session: AsyncSession, account_id: int
) -> AccountKnowledgeBinding | None:
    return await session.scalar(
        select(AccountKnowledgeBinding)
        .where(
            AccountKnowledgeBinding.account_id == account_id,
            AccountKnowledgeBinding.binding_type == "primary_brand",
            AccountKnowledgeBinding.status == "active",
        )
        .order_by(AccountKnowledgeBinding.id.desc())
    )


async def bind_account_primary_knowledge_base(
    session: AsyncSession,
    user: User,
    *,
    account_id: int,
    knowledge_base_id: int,
) -> AccountKnowledgeBinding:
    """Upsert the sole active primary binding with server-derived scope fields."""

    account = await require_account_knowledge_scope(session, user, account_id, writable=True)
    locked_account = await session.scalar(
        select(Account)
        .where(Account.id == account.id, Account.org_id == user.org_id)
        .with_for_update()
    )
    if locked_account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账号不存在")
    account = locked_account
    base = await session.get(KnowledgeBase, knowledge_base_id)
    if (
        base is None
        or base.org_id != user.org_id
        or base.status != "active"
        or base.kind != "brand"
        or base.client_id != account.client_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")

    current = await get_account_primary_binding(session, account.id)
    if current is not None and current.knowledge_base_id == base.id:
        return current
    if current is not None:
        current.status = "inactive"
        await session.flush()

    binding = AccountKnowledgeBinding(
        org_id=account.org_id,
        account_id=account.id,
        knowledge_base_id=base.id,
        knowledge_base_kind=base.kind,
        client_id=account.client_id,
        binding_type="primary_brand",
        status="active",
        bound_by_id=user.id,
    )
    session.add(binding)
    await session.flush()
    return binding


async def list_scoped_knowledge_base_entries(
    session: AsyncSession,
    user: User,
    *,
    knowledge_base_id: int,
    limit: int,
    offset: int,
) -> tuple[KnowledgeBase, list[KnowledgeEntry], int]:
    base = await get_scoped_knowledge_base(session, user, knowledge_base_id, writable=False)
    query = select(KnowledgeEntry).where(
        KnowledgeEntry.org_id == user.org_id,
        KnowledgeEntry.knowledge_base_id == base.id,
        KnowledgeEntry.status == "active",
    )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = list(
        await session.scalars(
            query.order_by(KnowledgeEntry.updated_at.desc(), KnowledgeEntry.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return base, rows, total or 0


async def get_scoped_knowledge_base_entry(
    session: AsyncSession,
    user: User,
    *,
    knowledge_base_id: int,
    entry_id: int,
    writable: bool,
) -> tuple[KnowledgeBase, KnowledgeEntry]:
    base = await get_scoped_knowledge_base(session, user, knowledge_base_id, writable=writable)
    entry = await session.get(KnowledgeEntry, entry_id)
    if (
        entry is None
        or entry.org_id != user.org_id
        or entry.knowledge_base_id != base.id
        or entry.status != "active"
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    return base, entry


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


async def list_agent_knowledge_for_account(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
    project_id: int,
    limit: int = _MAX_AGENT_KNOWLEDGE_LIMIT,
) -> list[KnowledgeEntry]:
    """Resolve current, account-bound evidence in one consistent database read.

    ``KnowledgeEntry`` has no account foreign key.  Its local layer is therefore
    intentionally limited to unbound entries in the account's client and exact
    project; it must not be represented as account-exclusive data.
    """

    bounded_limit = min(max(limit, 0), _MAX_AGENT_KNOWLEDGE_LIMIT)
    if bounded_limit == 0:
        return []

    primary_binding = aliased(AccountKnowledgeBinding)
    primary_base = aliased(KnowledgeBase)
    shared_binding = aliased(AccountKnowledgeBinding)
    shared_base = aliased(KnowledgeBase)
    now = datetime.now(UTC)

    primary_match = exists(
        select(primary_binding.id)
        .join(primary_base, primary_base.id == primary_binding.knowledge_base_id)
        .where(
            primary_binding.org_id == org_id,
            primary_binding.account_id == account_id,
            primary_binding.binding_type == "primary_brand",
            primary_binding.status == "active",
            primary_base.kind == "brand",
            primary_base.status == "active",
            KnowledgeEntry.knowledge_base_id == primary_binding.knowledge_base_id,
        )
    )
    shared_match = exists(
        select(shared_binding.id)
        .join(shared_base, shared_base.id == shared_binding.knowledge_base_id)
        .where(
            shared_binding.org_id == org_id,
            shared_binding.account_id == account_id,
            shared_binding.binding_type == "shared",
            shared_binding.status == "active",
            shared_base.kind == "organization_shared",
            shared_base.status == "active",
            KnowledgeEntry.knowledge_base_id == shared_binding.knowledge_base_id,
        )
    )
    local_match = KnowledgeEntry.knowledge_base_id.is_(None)
    source_tier = case(
        (local_match, 0),
        (primary_match, 1),
        (shared_match, 2),
        else_=3,
    )

    query = (
        select(KnowledgeEntry)
        .join(Account, Account.id == account_id)
        .join(
            Project,
            Project.id == project_id,
        )
        .where(
            Account.org_id == org_id,
            Project.org_id == org_id,
            Project.client_id == Account.client_id,
            KnowledgeEntry.org_id == org_id,
            KnowledgeEntry.client_id == Account.client_id,
            KnowledgeEntry.project_id == project_id,
            KnowledgeEntry.status == "active",
            KnowledgeEntry.verification_status == "verified",
            or_(KnowledgeEntry.effective_at.is_(None), KnowledgeEntry.effective_at <= now),
            or_(KnowledgeEntry.expires_at.is_(None), KnowledgeEntry.expires_at > now),
            or_(
                ~KnowledgeEntry.entry_kind.in_(("product_fact", "case")),
                KnowledgeEntry.allowed_for_external_claim.is_(True),
            ),
            or_(local_match, primary_match, shared_match),
        )
        .order_by(source_tier.asc(), KnowledgeEntry.id.asc())
        .limit(bounded_limit * 3)
    )
    rows = list(await session.scalars(query))
    return [row for row in rows if _claim_is_permitted(row)][:bounded_limit]


def _claim_is_permitted(row: KnowledgeEntry) -> bool:
    """Require explicit permission before a claim-like record reaches an Agent."""

    payload = row.payload if isinstance(row.payload, dict) else {}
    payload_kind = str(payload.get("kind", ""))
    is_numeric_claim = any(
        isinstance(payload.get(key), (int, float)) and not isinstance(payload.get(key), bool)
        for key in ("amount", "price", "value", "quantity")
    )
    needs_permission = (
        row.entry_kind in _EXTERNAL_CLAIM_KINDS
        or payload_kind in _EXTERNAL_CLAIM_KINDS
        or is_numeric_claim
    )
    return not needs_permission or row.allowed_for_external_claim


def knowledge_context(rows: list[KnowledgeEntry]) -> dict[str, list[dict]]:
    """Build the canonical user-context envelope for untrusted knowledge evidence."""

    evidence = [
        {
            "category": row.category.value,
            "content": row.content,
            "citation": {
                "entry_id": row.id,
                "entry_version": row.version,
                "source_label": row.source_label,
                "source_url": row.source_url,
                "source_type": row.source_type,
                "verification_status": row.verification_status,
                "allowed_for_external_claim": row.allowed_for_external_claim,
            },
            "tags": row.tags or [],
            "title": row.title,
        }
        for row in rows
    ]
    context: dict[str, list[dict]] = {"untrusted_evidence": evidence}
    for item in evidence:
        context.setdefault(str(item["category"]), []).append(item)
    return context


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
            entry_version=row.version,
            source_type=row.source_type,
            source_label=row.source_label,
            source_url=row.source_url,
            verification_status=row.verification_status,
            allowed_for_external_claim=row.allowed_for_external_claim,
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
