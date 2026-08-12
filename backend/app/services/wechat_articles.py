"""Working-copy and version operations for structured WeChat articles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.workspace_access import accessible_account_clause
from app.models import (
    Account,
    ArticleVersionCitation,
    ArticleWorkingCopy,
    ContentItem,
    Deliverable,
    KnowledgeCitation,
    User,
)
from app.models.enums import DeliverableType, Platform
from app.schemas.deliverable import validate_payload
from app.schemas.wechat_article import (
    ArticleDocument,
    ArticleSyncReadiness,
    ReadinessIssue,
)


@dataclass(frozen=True)
class ArticleVersionConflict(Exception):
    """A caller attempted to save an obsolete working-copy revision."""

    current_lock_version: int


@dataclass(frozen=True)
class ArticleFreezeConflict(Exception):
    """The immutable version sequence changed during a freeze attempt."""

    current_version: int


class ArticleCitationScopeError(ValueError):
    """A document declared missing or cross-scope evidence."""


class ArticleVersionNotFound(LookupError):
    """The requested immutable article version does not exist."""


ARTICLE_VERSION_AGENT_CODE = "02-content-director"
ArticleVersionTrigger = Literal[
    "first_ai_draft",
    "whole_article_ai_rewrite",
    "explicit_save_version",
    "pre_sync_freeze",
    "successful_sync_snapshot",
]


def _validated_article_payload(document: dict) -> dict:
    """Validate immutable snapshots through the shared deliverable registry."""
    return validate_payload(DeliverableType.WECHAT_ARTICLE, {"document": document}).model_dump(
        mode="json"
    )


def _validated_article_document(document: ArticleDocument | dict) -> ArticleDocument:
    """Reconstruct a strict document before using any source fields."""
    return ArticleDocument.model_validate(document)


def _declared_citation_ids(document: ArticleDocument) -> set[int]:
    return {citation_id for claim in document.claims for citation_id in claim.citation_ids}


async def _map_article_version_citations(
    session: AsyncSession,
    *,
    deliverable: Deliverable,
    document: ArticleDocument,
    account: Account,
) -> None:
    citation_ids = _declared_citation_ids(document)
    if not citation_ids:
        return
    if account.client_id is None:
        raise ArticleCitationScopeError("article account has no client evidence scope")
    citations = list(
        await session.scalars(
            select(KnowledgeCitation).where(KnowledgeCitation.id.in_(citation_ids))
        )
    )
    if {citation.id for citation in citations} != citation_ids:
        raise ArticleCitationScopeError("article citation does not exist")
    if any(
        citation.org_id != account.org_id or citation.client_id != account.client_id
        for citation in citations
    ):
        raise ArticleCitationScopeError("article citation is outside the account scope")
    session.add_all(
        [
            ArticleVersionCitation(
                deliverable_id=deliverable.id,
                knowledge_citation_id=citation_id,
            )
            for citation_id in sorted(citation_ids)
        ]
    )
    await session.flush()


async def _load_article_for_user(
    session: AsyncSession, user: User, content_item_id: int
) -> tuple[ArticleWorkingCopy, ContentItem, Account] | None:
    """Resolve the complete article lineage at one account-access boundary."""
    row = await session.execute(
        select(ArticleWorkingCopy, ContentItem, Account)
        .join(ContentItem, ArticleWorkingCopy.content_item_id == ContentItem.id)
        .join(Account, ArticleWorkingCopy.account_id == Account.id)
        .where(
            ContentItem.id == content_item_id,
            ContentItem.account_id == Account.id,
            Account.org_id == user.org_id,
            await accessible_account_clause(session, user),
        )
    )
    return cast(tuple[ArticleWorkingCopy, ContentItem, Account] | None, row.one_or_none())


async def _lock_article_for_user(
    session: AsyncSession, user: User, content_item_id: int
) -> tuple[ContentItem, Account, ArticleWorkingCopy] | None:
    """Lock article lineage before allocating an immutable version number."""
    row = await session.execute(
        select(ContentItem, Account, ArticleWorkingCopy)
        .join(Account, ContentItem.account_id == Account.id)
        .join(ArticleWorkingCopy, ArticleWorkingCopy.content_item_id == ContentItem.id)
        .where(
            ContentItem.id == content_item_id,
            ContentItem.account_id == Account.id,
            ArticleWorkingCopy.account_id == Account.id,
            Account.org_id == user.org_id,
            await accessible_account_clause(session, user),
        )
        .with_for_update(of=ContentItem)
    )
    return cast(tuple[ContentItem, Account, ArticleWorkingCopy] | None, row.one_or_none())


async def create_article(
    session: AsyncSession,
    user: User,
    *,
    account_id: int,
    document: ArticleDocument,
) -> tuple[ContentItem, ArticleWorkingCopy, Deliverable] | None:
    """Create the mutable article and its immutable first-AI-draft snapshot."""
    account = await session.scalar(
        select(Account).where(
            Account.id == account_id,
            Account.org_id == user.org_id,
            Account.platform == Platform.WECHAT_OFFICIAL_ACCOUNT,
            await accessible_account_clause(session, user),
        )
    )
    if account is None:
        return None
    validated_document = _validated_article_document(document)
    payload = _validated_article_payload(validated_document.model_dump(mode="json"))
    async with session.begin_nested():
        content_item = ContentItem(
            account_id=account.id,
            created_by_id=user.id,
            title=validated_document.title,
        )
        working_copy = ArticleWorkingCopy(
            content_item=content_item,
            account_id=account.id,
            document=validated_document.model_dump(mode="json"),
            updated_by_id=user.id,
        )
        first_version = Deliverable(
            content_item=content_item,
            agent_code=ARTICLE_VERSION_AGENT_CODE,
            type=DeliverableType.WECHAT_ARTICLE,
            version=1,
            payload=payload,
            note="article_version:first_ai_draft",
        )
        session.add_all([content_item, working_copy, first_version])
        await session.flush()
        await _map_article_version_citations(
            session,
            deliverable=first_version,
            document=validated_document,
            account=account,
        )
        working_copy.based_on_deliverable_id = first_version.id
    await session.commit()
    await session.refresh(content_item)
    await session.refresh(working_copy)
    await session.refresh(first_version)
    return content_item, working_copy, first_version


async def update_working_copy(
    session: AsyncSession,
    user: User,
    *,
    content_item_id: int,
    expected_lock_version: int,
    document: ArticleDocument,
) -> ArticleWorkingCopy | None:
    """Save a working copy only if the caller still owns its observed revision."""
    article = await _load_article_for_user(session, user, content_item_id)
    if article is None:
        return None
    working_copy, _content_item, _account = article
    result = await session.execute(
        update(ArticleWorkingCopy)
        .where(
            ArticleWorkingCopy.id == working_copy.id,
            ArticleWorkingCopy.lock_version == expected_lock_version,
        )
        .values(
            document=document.model_dump(mode="json"),
            lock_version=ArticleWorkingCopy.lock_version + 1,
            updated_by_id=user.id,
        )
    )
    if getattr(result, "rowcount", 0) != 1:
        current_lock_version = await session.scalar(
            select(ArticleWorkingCopy.lock_version).where(ArticleWorkingCopy.id == working_copy.id)
        )
        raise ArticleVersionConflict(current_lock_version or working_copy.lock_version)
    await session.commit()
    await session.refresh(working_copy)
    return working_copy


async def freeze_article_version(
    session: AsyncSession,
    user: User,
    *,
    content_item_id: int,
    trigger: ArticleVersionTrigger,
) -> Deliverable | None:
    """Create the next immutable document snapshot under the article row lock."""
    article = await _lock_article_for_user(session, user, content_item_id)
    if article is None:
        return None
    content_item, account, working_copy = article
    validated_document = _validated_article_document(working_copy.document)
    current_version = await session.scalar(
        select(func.max(Deliverable.version)).where(
            Deliverable.content_item_id == content_item.id,
            Deliverable.agent_code == ARTICLE_VERSION_AGENT_CODE,
            Deliverable.type == DeliverableType.WECHAT_ARTICLE,
        )
    )
    next_version = (current_version or 0) + 1
    deliverable = Deliverable(
        content_item_id=content_item.id,
        agent_code=ARTICLE_VERSION_AGENT_CODE,
        type=DeliverableType.WECHAT_ARTICLE,
        version=next_version,
        payload=_validated_article_payload(validated_document.model_dump(mode="json")),
        note=f"article_version:{trigger}",
    )
    try:
        async with session.begin_nested():
            session.add(deliverable)
            await session.flush()
            await _map_article_version_citations(
                session,
                deliverable=deliverable,
                document=validated_document,
                account=account,
            )
    except IntegrityError as exc:
        current_version = await session.scalar(
            select(func.max(Deliverable.version)).where(
                Deliverable.content_item_id == content_item.id,
                Deliverable.agent_code == ARTICLE_VERSION_AGENT_CODE,
                Deliverable.type == DeliverableType.WECHAT_ARTICLE,
            )
        )
        raise ArticleFreezeConflict(current_version or 0) from exc
    working_copy.based_on_deliverable_id = deliverable.id
    await session.commit()
    await session.refresh(deliverable)
    await session.refresh(working_copy)
    return deliverable


async def snapshot_successful_sync(
    session: AsyncSession, user: User, *, content_item_id: int
) -> Deliverable | None:
    """Service entry for later sync tasks; it performs no external WeChat write."""
    return await freeze_article_version(
        session,
        user,
        content_item_id=content_item_id,
        trigger="successful_sync_snapshot",
    )


async def snapshot_completed_whole_article_rewrite(
    session: AsyncSession, user: User, *, content_item_id: int
) -> Deliverable | None:
    """Persist the immutable snapshot created by a completed whole-article AI rewrite."""
    return await freeze_article_version(
        session,
        user,
        content_item_id=content_item_id,
        trigger="whole_article_ai_rewrite",
    )


async def freeze_before_sync(
    session: AsyncSession, user: User, *, content_item_id: int
) -> Deliverable | None:
    """Persist the source snapshot that a later sync task is authorized to use."""
    return await freeze_article_version(
        session,
        user,
        content_item_id=content_item_id,
        trigger="pre_sync_freeze",
    )


def diff_versions(base_document: ArticleDocument, target_document: ArticleDocument) -> dict:
    """Return a deterministic block-level structured diff without rendering HTML."""
    base_blocks = {block.block_id: block.model_dump(mode="json") for block in base_document.blocks}
    target_blocks = {
        block.block_id: block.model_dump(mode="json") for block in target_document.blocks
    }
    base_ids = list(base_blocks)
    target_ids = list(target_blocks)
    shared_ids = set(base_ids) & set(target_ids)
    lcs_ids = _longest_common_subsequence(
        [block_id for block_id in base_ids if block_id in shared_ids],
        [block_id for block_id in target_ids if block_id in shared_ids],
    )
    changed = [
        block_id
        for block_id in target_ids
        if block_id in shared_ids and base_blocks[block_id] != target_blocks[block_id]
    ]
    all_blocks = base_blocks | target_blocks
    text_ids = {block_id for block_id, block in all_blocks.items() if _block_has_text(block)}
    semantic_changes = {
        block_id
        for block_id in text_ids
        if base_blocks.get(block_id) != target_blocks.get(block_id)
    }
    return {
        "added": [block_id for block_id in target_ids if block_id not in base_blocks],
        "removed": [block_id for block_id in base_ids if block_id not in target_blocks],
        "moved": [
            block_id
            for block_id in target_ids
            if block_id in shared_ids and block_id not in lcs_ids
        ],
        "changed": changed,
        "textSemanticChangeRatio": len(semantic_changes) / len(text_ids) if text_ids else 0.0,
    }


_EXTERNAL_CLAIM_CODES = {
    "product_fact": "UNRESOLVED_PRODUCT_CLAIM",
    "case": "UNRESOLVED_CASE_CLAIM",
    "promise": "UNRESOLVED_PROMISE_CLAIM",
    "price": "UNRESOLVED_PRICE_CLAIM",
    "numeric": "UNRESOLVED_NUMERIC_CLAIM",
}


async def validate_article_for_sync(
    session: AsyncSession,
    *,
    version_id: int,
    quality_review_available: bool = True,
) -> ArticleSyncReadiness:
    """Read only the immutable version and its exact historical evidence snapshots."""
    row = await session.execute(
        select(Deliverable, ContentItem, Account)
        .join(ContentItem, Deliverable.content_item_id == ContentItem.id)
        .join(Account, ContentItem.account_id == Account.id)
        .where(
            Deliverable.id == version_id,
            Deliverable.agent_code == ARTICLE_VERSION_AGENT_CODE,
            Deliverable.type == DeliverableType.WECHAT_ARTICLE,
            ContentItem.account_id == Account.id,
        )
    )
    lineage = cast(tuple[Deliverable, ContentItem, Account] | None, row.one_or_none())
    if lineage is None:
        raise ArticleVersionNotFound("WeChat article version not found")
    deliverable, _content_item, account = lineage
    document = _validated_article_document(deliverable.payload["document"])
    evidence_rows = await session.execute(
        select(ArticleVersionCitation, KnowledgeCitation)
        .join(
            KnowledgeCitation,
            ArticleVersionCitation.knowledge_citation_id == KnowledgeCitation.id,
        )
        .where(ArticleVersionCitation.deliverable_id == deliverable.id)
    )
    citations = {citation.id: citation for _mapping, citation in evidence_rows.all()}
    blockers: list[ReadinessIssue] = []
    warnings: list[ReadinessIssue] = []
    unresolved_claims: set[str] = set()
    now = datetime.now(UTC)
    for claim in document.claims:
        if claim.kind == "public_info":
            if not claim.citation_ids or any(cid not in citations for cid in claim.citation_ids):
                warnings.append(
                    ReadinessIssue(
                        code="UNVERIFIED_PUBLIC_INFO",
                        message="Public information has no traceable version evidence.",
                        claim_id=claim.claim_id,
                    )
                )
            continue
        invalid = not claim.citation_ids
        for citation_id in claim.citation_ids:
            citation = citations.get(citation_id)
            invalid = invalid or citation is None or not _citation_supports_external_claim(
                citation, account=account, now=now
            )
        if invalid:
            unresolved_claims.add(claim.claim_id)
            blockers.append(
                ReadinessIssue(
                    code=_EXTERNAL_CLAIM_CODES[claim.kind],
                    message="External claim lacks valid immutable evidence.",
                    claim_id=claim.claim_id,
                )
            )
    if not quality_review_available:
        warnings.append(
            ReadinessIssue(
                code="QUALITY_REVIEW_UNAVAILABLE",
                message="Quality review was unavailable; no score was synthesized.",
            )
        )
    return ArticleSyncReadiness(
        can_sync=not blockers,
        blockers=blockers,
        warnings=warnings,
        citation_count=len(citations),
        unresolved_claim_count=len(unresolved_claims),
    )


def _citation_supports_external_claim(
    citation: KnowledgeCitation, *, account: Account, now: datetime
) -> bool:
    if citation.org_id != account.org_id or account.client_id is None:
        return False
    if citation.client_id != account.client_id:
        return False
    required_snapshots = (
        citation.entry_version,
        citation.source_type,
        citation.source_label,
        citation.verification_status,
        citation.allowed_for_external_claim,
        citation.effective_at,
        citation.expires_at,
    )
    if any(value is None for value in required_snapshots):
        return False
    assert citation.effective_at is not None
    assert citation.expires_at is not None
    effective_at = _as_utc(citation.effective_at)
    expires_at = _as_utc(citation.expires_at)
    return bool(
        citation.verification_status == "verified"
        and citation.allowed_for_external_claim is True
        and effective_at <= now < expires_at
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _block_has_text(block: dict) -> bool:
    return any(key in block for key in ("text", "items", "label"))


def _longest_common_subsequence(first: list[str], second: list[str]) -> set[str]:
    """Return an order-stable LCS set for stable moved-block detection."""
    table = [[0] * (len(second) + 1) for _ in range(len(first) + 1)]
    for first_index, first_id in enumerate(first, start=1):
        for second_index, second_id in enumerate(second, start=1):
            table[first_index][second_index] = (
                table[first_index - 1][second_index - 1] + 1
                if first_id == second_id
                else max(table[first_index - 1][second_index], table[first_index][second_index - 1])
            )
    result: set[str] = set()
    first_index, second_index = len(first), len(second)
    while first_index and second_index:
        if first[first_index - 1] == second[second_index - 1]:
            result.add(first[first_index - 1])
            first_index -= 1
            second_index -= 1
        elif table[first_index - 1][second_index] >= table[first_index][second_index - 1]:
            first_index -= 1
        else:
            second_index -= 1
    return result
