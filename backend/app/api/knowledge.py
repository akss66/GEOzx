"""Reviewed knowledge documents, brand libraries, and account bindings."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.workspace_access import require_client_access
from app.db import get_session
from app.models import AccountKnowledgeBinding, KnowledgeBase, KnowledgeCitation, KnowledgeEntry
from app.models.enums import KnowledgeCategory, UserRole
from app.schemas.knowledge import (
    AccountKnowledgeBindingOut,
    BindAccountKnowledgeRequest,
    CreateKnowledgeBaseEntryRequest,
    CreateKnowledgeRequest,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseEntryListOut,
    KnowledgeBaseEntryOut,
    KnowledgeBaseListOut,
    KnowledgeBaseOut,
    KnowledgeBaseUpdateRequest,
    KnowledgeCitationOut,
    KnowledgeOut,
    PaginationOut,
    ProductFactPayload,
    UpdateKnowledgeBaseEntryRequest,
    UpdateKnowledgeRequest,
)
from app.services.knowledge_workspace import (
    FACT_REVIEW_ROLES,
    bind_account_primary_knowledge_base,
    get_account_primary_binding,
    get_scoped_entry,
    get_scoped_knowledge_base,
    get_scoped_knowledge_base_entry,
    knowledge_event,
    list_scoped_knowledge,
    list_scoped_knowledge_base_entries,
    list_scoped_knowledge_bases,
    require_account_knowledge_scope,
    require_knowledge_scope,
)

router = APIRouter(tags=["knowledge"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/knowledge", response_model=list[KnowledgeOut])
async def list_knowledge(
    user: CurrentUser,
    session: SessionDep,
    client_id: Annotated[int, Query(gt=0)],
    project_id: Annotated[int | None, Query(gt=0)] = None,
    category: Annotated[KnowledgeCategory | None, Query()] = None,
) -> list[KnowledgeOut]:
    rows = await list_scoped_knowledge(
        session,
        user,
        client_id=client_id,
        project_id=project_id,
        category=category,
    )
    return [KnowledgeOut.model_validate(row) for row in rows]


@router.post("/knowledge", response_model=KnowledgeOut, status_code=status.HTTP_201_CREATED)
async def create_knowledge(
    body: CreateKnowledgeRequest, user: CurrentUser, session: SessionDep
) -> KnowledgeOut:
    await require_knowledge_scope(
        session,
        user,
        body.client_id,
        body.project_id,
        writable=True,
    )
    if body.source_type == "agent":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Agent 建议必须经过人工确认后才能写入知识库",
        )
    entry = KnowledgeEntry(
        org_id=user.org_id,
        client_id=body.client_id,
        project_id=body.project_id,
        category=body.category,
        title=body.title,
        content=body.content,
        payload=body.payload,
        tags=body.tags,
        source_type=body.source_type,
        source_label=body.source_label,
        source_url=str(body.source_url) if body.source_url else None,
        version=1,
        status="active",
        created_by_id=user.id,
    )
    session.add(entry)
    await session.flush()
    session.add(
        knowledge_event(
            "knowledge.created",
            project_id=entry.project_id,
            entry_id=entry.id,
            actor_user_id=user.id,
        )
    )
    await session.commit()
    await session.refresh(entry)
    return KnowledgeOut.model_validate(entry)


@router.patch("/knowledge/{entry_id}", response_model=KnowledgeOut)
async def update_knowledge(
    entry_id: int, body: UpdateKnowledgeRequest, user: CurrentUser, session: SessionDep
) -> KnowledgeOut:
    entry = await get_scoped_entry(session, user, entry_id, writable=True)
    data = body.model_dump(exclude_unset=True)
    if entry.entry_kind == "product_fact" and data:
        try:
            payload = ProductFactPayload.model_validate(body.payload or entry.payload)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="产品事实格式无效"
            ) from exc
        if body.payload is not None:
            data["payload"] = payload.model_dump()
        data["allowed_for_external_claim"] = payload.allowed_for_external_claim
    if "source_url" in data and data["source_url"] is not None:
        data["source_url"] = str(data["source_url"])
    if data:
        for key, value in data.items():
            setattr(entry, key, value)
        entry.version += 1
        session.add(
            knowledge_event(
                "knowledge.updated",
                project_id=entry.project_id,
                entry_id=entry.id,
                actor_user_id=user.id,
            )
        )
        await session.commit()
        await session.refresh(entry)
    return KnowledgeOut.model_validate(entry)


@router.delete("/knowledge/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_knowledge(entry_id: int, user: CurrentUser, session: SessionDep) -> None:
    entry = await get_scoped_entry(session, user, entry_id, writable=True)
    entry.status = "archived"
    entry.version += 1
    session.add(
        knowledge_event(
            "knowledge.archived",
            project_id=entry.project_id,
            entry_id=entry.id,
            actor_user_id=user.id,
        )
    )
    await session.commit()


@router.get("/knowledge/{entry_id}/citations", response_model=list[KnowledgeCitationOut])
async def list_knowledge_citations(
    entry_id: int,
    user: CurrentUser,
    session: SessionDep,
    client_id: Annotated[int, Query(gt=0)],
    project_id: Annotated[int | None, Query(gt=0)] = None,
) -> list[KnowledgeCitationOut]:
    entry = await get_scoped_entry(session, user, entry_id, writable=False)
    if entry.client_id != client_id or (
        entry.project_id is not None and entry.project_id != project_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识条目不存在")
    q = select(KnowledgeCitation).where(
        KnowledgeCitation.entry_id == entry.id,
        KnowledgeCitation.client_id == client_id,
    )
    if project_id is not None:
        q = q.where(KnowledgeCitation.project_id == project_id)
    rows = await session.scalars(q.order_by(KnowledgeCitation.id.desc()))
    return [KnowledgeCitationOut.model_validate(row) for row in rows]


@router.post(
    "/knowledge-bases", response_model=KnowledgeBaseOut, status_code=status.HTTP_201_CREATED
)
async def create_knowledge_base(
    body: KnowledgeBaseCreateRequest, user: CurrentUser, session: SessionDep
) -> KnowledgeBaseOut:
    if body.client_id is None:
        if user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="无权管理组织共享知识库"
            )
    else:
        await require_knowledge_scope(session, user, body.client_id, None, writable=True)
    base = KnowledgeBase(
        org_id=user.org_id,
        client_id=body.client_id,
        kind=body.kind,
        name=body.name,
        description=body.description,
        status="active",
        version=1,
        created_by_id=user.id,
    )
    session.add(base)
    await session.commit()
    await session.refresh(base)
    return KnowledgeBaseOut.model_validate(base)


@router.get("/knowledge-bases", response_model=KnowledgeBaseListOut)
async def list_knowledge_bases(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> KnowledgeBaseListOut:
    rows, total = await list_scoped_knowledge_bases(session, user, limit=limit, offset=offset)
    return KnowledgeBaseListOut(
        data=[KnowledgeBaseOut.model_validate(row) for row in rows],
        pagination=PaginationOut(limit=limit, offset=offset, total=total),
    )


@router.get("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseOut)
async def get_knowledge_base(
    knowledge_base_id: int, user: CurrentUser, session: SessionDep
) -> KnowledgeBaseOut:
    base = await get_scoped_knowledge_base(session, user, knowledge_base_id, writable=False)
    return KnowledgeBaseOut.model_validate(base)


@router.patch("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseOut)
async def update_knowledge_base(
    knowledge_base_id: int,
    body: KnowledgeBaseUpdateRequest,
    user: CurrentUser,
    session: SessionDep,
) -> KnowledgeBaseOut:
    base = await get_scoped_knowledge_base(session, user, knowledge_base_id, writable=True)
    data = body.model_dump(exclude_unset=True)
    if data:
        for key, value in data.items():
            setattr(base, key, value)
        base.version += 1
        await session.commit()
        await session.refresh(base)
    return KnowledgeBaseOut.model_validate(base)


@router.delete("/knowledge-bases/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_knowledge_base(
    knowledge_base_id: int, user: CurrentUser, session: SessionDep
) -> None:
    base = await get_scoped_knowledge_base(session, user, knowledge_base_id, writable=True)
    base.status = "archived"
    base.version += 1
    active_bindings = await session.scalars(
        select(AccountKnowledgeBinding).where(
            AccountKnowledgeBinding.knowledge_base_id == base.id,
            AccountKnowledgeBinding.status == "active",
        )
    )
    for binding in active_bindings:
        binding.status = "inactive"
    await session.commit()


@router.get(
    "/knowledge-bases/{knowledge_base_id}/entries", response_model=KnowledgeBaseEntryListOut
)
async def list_knowledge_base_entries(
    knowledge_base_id: int,
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> KnowledgeBaseEntryListOut:
    _base, rows, total = await list_scoped_knowledge_base_entries(
        session, user, knowledge_base_id=knowledge_base_id, limit=limit, offset=offset
    )
    return KnowledgeBaseEntryListOut(
        data=[KnowledgeBaseEntryOut.model_validate(row) for row in rows],
        pagination=PaginationOut(limit=limit, offset=offset, total=total),
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/entries",
    response_model=KnowledgeBaseEntryOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base_entry(
    knowledge_base_id: int,
    body: CreateKnowledgeBaseEntryRequest,
    user: CurrentUser,
    session: SessionDep,
) -> KnowledgeBaseEntryOut:
    base = await get_scoped_knowledge_base(session, user, knowledge_base_id, writable=True)
    payload = body.payload.model_dump()
    entry = KnowledgeEntry(
        org_id=base.org_id,
        client_id=base.client_id,
        project_id=None,
        knowledge_base_id=base.id,
        category=body.category,
        title=body.title,
        content=body.content,
        payload=payload,
        tags=body.tags,
        source_type="manual",
        source_label=body.source_label,
        source_url=str(body.source_url) if body.source_url else None,
        version=1,
        status="active",
        created_by_id=user.id,
        entry_kind=body.entry_kind,
        verification_status="draft",
        source_attachment_id=body.source_attachment_id,
        effective_at=body.effective_at,
        expires_at=body.expires_at,
        allowed_for_external_claim=(
            payload.get("allowed_for_external_claim", False)
            if body.entry_kind == "product_fact"
            else False
        ),
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return KnowledgeBaseEntryOut.model_validate(entry)


@router.patch(
    "/knowledge-bases/{knowledge_base_id}/entries/{entry_id}", response_model=KnowledgeBaseEntryOut
)
async def update_knowledge_base_entry(
    knowledge_base_id: int,
    entry_id: int,
    body: UpdateKnowledgeBaseEntryRequest,
    user: CurrentUser,
    session: SessionDep,
) -> KnowledgeBaseEntryOut:
    verification_change = body.verification_status is not None
    base, entry = await get_scoped_knowledge_base_entry(
        session,
        user,
        knowledge_base_id=knowledge_base_id,
        entry_id=entry_id,
        writable=not verification_change,
    )
    if verification_change:
        if base.client_id is None:
            if user.role != UserRole.ADMIN:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="无权审核知识条目"
                )
        else:
            await require_client_access(session, user, base.client_id, roles=FACT_REVIEW_ROLES)
    data = body.model_dump(exclude_unset=True)
    if body.payload is not None and body.payload.kind != entry.entry_kind:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="条目类型不可变"
        )
    if "payload" in data and data["payload"] is not None:
        data["payload"] = body.payload.model_dump() if body.payload is not None else None
    if entry.entry_kind == "product_fact":
        try:
            fact_payload = ProductFactPayload.model_validate(data.get("payload", entry.payload))
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="产品事实格式无效"
            ) from exc
        data["allowed_for_external_claim"] = fact_payload.allowed_for_external_claim
    else:
        data["allowed_for_external_claim"] = False
    if "source_url" in data and data["source_url"] is not None:
        data["source_url"] = str(data["source_url"])
    if verification_change:
        data["verified_by_id"] = user.id
        data["verified_at"] = datetime.now(UTC)
    if data:
        for key, value in data.items():
            setattr(entry, key, value)
        entry.version += 1
        await session.commit()
        await session.refresh(entry)
    return KnowledgeBaseEntryOut.model_validate(entry)


@router.delete(
    "/knowledge-bases/{knowledge_base_id}/entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def archive_knowledge_base_entry(
    knowledge_base_id: int, entry_id: int, user: CurrentUser, session: SessionDep
) -> None:
    _base, entry = await get_scoped_knowledge_base_entry(
        session,
        user,
        knowledge_base_id=knowledge_base_id,
        entry_id=entry_id,
        writable=True,
    )
    entry.status = "archived"
    entry.version += 1
    await session.commit()


@router.put("/accounts/{account_id}/knowledge-binding", response_model=AccountKnowledgeBindingOut)
async def bind_account_knowledge(
    account_id: int,
    body: BindAccountKnowledgeRequest,
    user: CurrentUser,
    session: SessionDep,
) -> AccountKnowledgeBindingOut:
    try:
        binding = await bind_account_primary_knowledge_base(
            session, user, account_id=account_id, knowledge_base_id=body.knowledge_base_id
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="品牌知识库绑定正在更新，请重试",
        ) from exc
    await session.refresh(binding)
    return AccountKnowledgeBindingOut.model_validate(binding)


@router.get("/accounts/{account_id}/knowledge-binding", response_model=AccountKnowledgeBindingOut)
async def get_account_knowledge_binding(
    account_id: int, user: CurrentUser, session: SessionDep
) -> AccountKnowledgeBindingOut:
    await require_account_knowledge_scope(session, user, account_id, writable=False)
    binding = await get_account_primary_binding(session, account_id)
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="品牌知识库绑定不存在")
    return AccountKnowledgeBindingOut.model_validate(binding)


@router.delete("/accounts/{account_id}/knowledge-binding", status_code=status.HTTP_204_NO_CONTENT)
async def unbind_account_knowledge(account_id: int, user: CurrentUser, session: SessionDep) -> None:
    await require_account_knowledge_scope(session, user, account_id, writable=True)
    binding = await get_account_primary_binding(session, account_id)
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="品牌知识库绑定不存在")
    binding.status = "inactive"
    await session.commit()
