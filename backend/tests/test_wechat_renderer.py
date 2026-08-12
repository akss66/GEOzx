"""Security rendering and immutable pre-sync evidence gates for WeChat articles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    Account,
    ArticleVersionCitation,
    Client,
    KnowledgeCitation,
    KnowledgeEntry,
)
from app.models.enums import KnowledgeCategory, Platform
from app.schemas.wechat_article import ArticleDocument
from app.services.wechat_articles import (
    ArticleCitationScopeError,
    create_article,
    validate_article_for_sync,
)
from app.services.wechat_renderer import WechatRenderError, render_wechat_article


def _document(
    *, title: str = "安全文章", author: str | None = "编辑", digest: str = "摘要"
) -> dict:
    return {
        "title": title,
        "author": author,
        "digest": digest,
        "blocks": [
            {"type": "heading", "block_id": "heading", "level": 2, "text": "标题"},
            {
                "type": "paragraph",
                "block_id": "paragraph",
                "text": '文本 & "引号"',
            },
            {"type": "imageSlot", "block_id": "image", "slot_key": "hero"},
            {
                "type": "cta",
                "block_id": "cta",
                "label": "了解更多",
                "action": "learn_more",
                "url": "https://example.com/read?a=1&b=2",
            },
        ],
    }


def test_renderer_escapes_text_and_emits_only_allowlisted_wechat_images():
    document = ArticleDocument.model_validate(_document())
    rendered = render_wechat_article(
        document, asset_map={"hero": "https://mmbiz.qpic.cn/trusted/image.jpg"}
    )

    assert "<script" not in rendered.html
    assert "onclick=" not in rendered.html
    assert "javascript:" not in rendered.html
    assert "文本 &amp; &quot;引号&quot;" in rendered.html
    assert "https://mmbiz.qpic.cn/trusted/image.jpg" in rendered.html
    assert rendered.normalized_html == rendered.html
    assert len(rendered.content_hash) == 64
    assert rendered == render_wechat_article(
        document, asset_map={"hero": "https://mmbiz.qpic.cn/trusted/image.jpg"}
    )


@pytest.mark.parametrize(
    "asset_map",
    [
        {},
        {"hero": "https://outside.example/image.png"},
        {"hero": "javascript:alert(1)"},
        {"hero": "data:image/png;base64,AAAA"},
    ],
)
def test_renderer_refuses_unresolved_or_untrusted_image_slots(asset_map):
    with pytest.raises(WechatRenderError):
        render_wechat_article(ArticleDocument.model_validate(_document()), asset_map=asset_map)


def test_renderer_refuses_non_https_cta_urls():
    document = _document()
    document["blocks"][-1]["url"] = "http://example.com/read"
    with pytest.raises(WechatRenderError):
        render_wechat_article(ArticleDocument.model_validate(document), asset_map={"hero": "https://mmbiz.qpic.cn/a"})


@pytest.mark.parametrize(
    ("field", "accepted", "rejected"),
    [
        ("title", "题" * 32, "题" * 33),
        ("author", "作" * 16, "作" * 17),
        ("digest", "摘" * 120, "摘" * 121),
    ],
)
def test_renderer_enforces_metadata_unicode_code_point_boundaries(field, accepted, rejected):
    valid = _document(**{field: accepted})
    render_wechat_article(
        ArticleDocument.model_validate(valid), asset_map={"hero": "https://mmbiz.qpic.cn/a"}
    )
    invalid = _document(**{field: rejected})
    with pytest.raises((WechatRenderError, ValueError)):
        render_wechat_article(
            ArticleDocument.model_validate(invalid), asset_map={"hero": "https://mmbiz.qpic.cn/a"}
        )


def test_renderer_enforces_strict_character_and_utf8_byte_content_limits(monkeypatch):
    document = ArticleDocument.model_validate(_document())
    monkeypatch.setattr("app.services.wechat_renderer._render_blocks", lambda *_: "a" * 19_999)
    render_wechat_article(document, asset_map={"hero": "https://mmbiz.qpic.cn/a"})
    monkeypatch.setattr("app.services.wechat_renderer._render_blocks", lambda *_: "a" * 20_000)
    with pytest.raises(WechatRenderError):
        render_wechat_article(document, asset_map={"hero": "https://mmbiz.qpic.cn/a"})
    monkeypatch.setattr(
        "app.services.wechat_renderer._render_blocks", lambda *_: "界" * 19_999
    )
    render_wechat_article(document, asset_map={"hero": "https://mmbiz.qpic.cn/a"})
    monkeypatch.setattr(
        "app.services.wechat_renderer._render_blocks", lambda *_: "界" * 349_526
    )
    monkeypatch.setattr("app.services.wechat_renderer.MAX_CONTENT_CHARACTERS", 400_000)
    with pytest.raises(WechatRenderError):
        render_wechat_article(document, asset_map={"hero": "https://mmbiz.qpic.cn/a"})


async def _citation(
    session, admin, client, **overrides
) -> tuple[KnowledgeEntry, KnowledgeCitation]:
    now = datetime.now(UTC)
    entry = KnowledgeEntry(
        org_id=admin.org_id,
        client_id=client.id,
        category=KnowledgeCategory.SCRIPT_LIBRARY,
        title="Verified product fact",
        content="Historical evidence",
        source_type="official_document",
        source_label="Product manual",
        source_url="https://example.test/manual",
        version=3,
        entry_kind="product_fact",
        verification_status="verified",
        effective_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=1),
        allowed_for_external_claim=True,
    )
    session.add(entry)
    await session.flush()
    values = {
        "org_id": admin.org_id,
        "client_id": client.id,
        "entry_id": entry.id,
        "entry_version": entry.version,
        "source_type": entry.source_type,
        "source_label": entry.source_label,
        "source_url": entry.source_url,
        "verification_status": entry.verification_status,
        "effective_at": entry.effective_at,
        "expires_at": entry.expires_at,
        "allowed_for_external_claim": entry.allowed_for_external_claim,
        "agent_code": "02-content-director",
        "context": "article claim",
    }
    values.update(overrides)
    citation = KnowledgeCitation(**values)
    session.add(citation)
    await session.flush()
    return entry, citation


def _claimed_document(citation_ids: list[int], *, kind: str = "product_fact") -> ArticleDocument:
    raw = _document()
    raw["blocks"] = [raw["blocks"][1]]
    raw["claims"] = [
        {
            "claim_id": "claim-1",
            "block_id": "paragraph",
            "kind": kind,
            "text": "This product reduces heat transfer by 30%.",
            "citation_ids": citation_ids,
        }
    ]
    return ArticleDocument.model_validate(raw)


@pytest.mark.asyncio
async def test_article_version_maps_only_exact_declared_citations(session, admin):
    client = Client(org_id=admin.org_id, name="Article client")
    account = Account(
        org_id=admin.org_id,
        client=client,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Article account",
    )
    session.add_all([client, account])
    await session.flush()
    _entry, selected = await _citation(session, admin, client)
    await _citation(session, admin, client)

    created = await create_article(
        session,
        admin,
        account_id=account.id,
        document=_claimed_document([selected.id]),
    )
    assert created is not None
    version = created[2]
    mapped = await session.scalars(
        select(ArticleVersionCitation).where(ArticleVersionCitation.deliverable_id == version.id)
    )
    assert [row.knowledge_citation_id for row in mapped] == [selected.id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"entry_version": None}, "UNRESOLVED_PRODUCT_CLAIM"),
        ({"source_type": None}, "UNRESOLVED_PRODUCT_CLAIM"),
        ({"source_label": None}, "UNRESOLVED_PRODUCT_CLAIM"),
        ({"verification_status": "draft"}, "UNRESOLVED_PRODUCT_CLAIM"),
        ({"allowed_for_external_claim": False}, "UNRESOLVED_PRODUCT_CLAIM"),
        ({"effective_at": datetime.now(UTC) + timedelta(days=1)}, "UNRESOLVED_PRODUCT_CLAIM"),
        ({"expires_at": datetime.now(UTC) - timedelta(seconds=1)}, "UNRESOLVED_PRODUCT_CLAIM"),
    ],
)
async def test_invalid_external_claim_snapshots_block_sync(
    session, admin, overrides, expected_code
):
    client = Client(org_id=admin.org_id, name="Article client")
    account = Account(
        org_id=admin.org_id,
        client=client,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Article account",
    )
    session.add_all([client, account])
    await session.flush()
    _entry, citation = await _citation(session, admin, client, **overrides)
    created = await create_article(
        session, admin, account_id=account.id, document=_claimed_document([citation.id])
    )
    assert created is not None

    readiness = await validate_article_for_sync(session, version_id=created[2].id)

    assert readiness.can_sync is False
    assert readiness.blockers[0].code == expected_code
    assert readiness.unresolved_claim_count == 1
    assert readiness.citation_count == 1


@pytest.mark.asyncio
async def test_unresolved_product_claim_and_public_info_have_distinct_gate_results(session, admin):
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Unscoped article account",
    )
    session.add(account)
    await session.flush()
    blocked = await create_article(
        session, admin, account_id=account.id, document=_claimed_document([])
    )
    warned = await create_article(
        session,
        admin,
        account_id=account.id,
        document=_claimed_document([], kind="public_info"),
    )
    assert blocked is not None and warned is not None

    blocked_readiness = await validate_article_for_sync(session, version_id=blocked[2].id)
    warned_readiness = await validate_article_for_sync(
        session, version_id=warned[2].id, quality_review_available=False
    )

    assert blocked_readiness.blockers[0].code == "UNRESOLVED_PRODUCT_CLAIM"
    assert warned_readiness.can_sync is True
    assert {issue.code for issue in warned_readiness.warnings} == {
        "UNVERIFIED_PUBLIC_INFO",
        "QUALITY_REVIEW_UNAVAILABLE",
    }
    assert warned_readiness.unresolved_claim_count == 0
    assert not hasattr(warned_readiness, "quality_score")


@pytest.mark.asyncio
async def test_readiness_replays_snapshot_not_mutated_knowledge_entry(session, admin):
    client = Client(org_id=admin.org_id, name="Article client")
    account = Account(
        org_id=admin.org_id,
        client=client,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Article account",
    )
    session.add_all([client, account])
    await session.flush()
    entry, citation = await _citation(session, admin, client)
    created = await create_article(
        session, admin, account_id=account.id, document=_claimed_document([citation.id])
    )
    assert created is not None
    entry.verification_status = "rejected"
    entry.allowed_for_external_claim = False
    entry.version += 1
    await session.commit()

    readiness = await validate_article_for_sync(session, version_id=created[2].id)

    assert readiness.can_sync is True
    assert readiness.blockers == []
    assert await session.scalar(select(func.count(ArticleVersionCitation.id))) == 1


@pytest.mark.asyncio
async def test_article_version_creation_rejects_missing_and_cross_client_citations(session, admin):
    first_client = Client(org_id=admin.org_id, name="First client")
    second_client = Client(org_id=admin.org_id, name="Second client")
    account = Account(
        org_id=admin.org_id,
        client=first_client,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="First account",
    )
    session.add_all([first_client, second_client, account])
    await session.flush()
    _entry, foreign_citation = await _citation(session, admin, second_client)
    await session.commit()
    first_client_id = first_client.id
    account_id = account.id
    foreign_citation_id = foreign_citation.id
    admin_id = admin.id

    with pytest.raises(ArticleCitationScopeError):
        await create_article(
            session,
            admin,
            account_id=account_id,
            document=_claimed_document([foreign_citation_id]),
        )
    await session.rollback()

    first_client = await session.get(Client, first_client_id)
    account = await session.get(Account, account_id)
    admin = await session.get(type(admin), admin_id)
    assert first_client is not None and account is not None and admin is not None
    with pytest.raises(ArticleCitationScopeError):
        await create_article(
            session,
            admin,
            account_id=account.id,
            document=_claimed_document([999_999]),
        )


@pytest.mark.asyncio
async def test_claim_reference_not_in_version_mapping_blocks_sync(session, admin):
    client = Client(org_id=admin.org_id, name="Article client")
    account = Account(
        org_id=admin.org_id,
        client=client,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Article account",
    )
    session.add_all([client, account])
    await session.flush()
    _entry, citation = await _citation(session, admin, client)
    created = await create_article(
        session, admin, account_id=account.id, document=_claimed_document([citation.id])
    )
    assert created is not None
    mapping = await session.scalar(
        select(ArticleVersionCitation).where(
            ArticleVersionCitation.deliverable_id == created[2].id
        )
    )
    assert mapping is not None
    await session.delete(mapping)
    await session.commit()

    readiness = await validate_article_for_sync(session, version_id=created[2].id)

    assert readiness.can_sync is False
    assert readiness.blockers[0].code == "UNRESOLVED_PRODUCT_CLAIM"
    assert readiness.citation_count == 0
