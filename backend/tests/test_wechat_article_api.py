"""HTTP contracts for account-scoped WeChat article working copies."""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from app.models import Account, ArticleWorkingCopy, ContentItem, Deliverable
from app.models.enums import DeliverableType, Platform
from app.schemas.wechat_article import ArticleDocument
from app.services import wechat_articles
from app.services.wechat_articles import (
    create_article,
    freeze_article_version,
    freeze_before_sync,
    snapshot_completed_whole_article_rewrite,
    snapshot_successful_sync,
)


def _document(*, paragraph: str = "Keep rooms cool.") -> dict:
    return {
        "title": "Summer window insulation guide",
        "digest": "Practical tips for a cooler home.",
        "author": "Editorial team",
        "blocks": [
            {"type": "heading", "block_id": "heading-intro", "level": 2, "text": "Start here"},
            {"type": "paragraph", "block_id": "paragraph-intro", "text": paragraph},
        ],
    }


async def _headers(client) -> dict[str, str]:
    response = await client.post(
        "/auth/login", json={"email": "admin@test.com", "password": "admin-pw-123"}
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_stale_working_copy_returns_structured_409(client, admin, session):
    """A stale editor must not overwrite a later autosave."""
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Article account",
    )
    session.add(account)
    await session.flush()
    article = ContentItem(account_id=account.id, title="Article")
    session.add(article)
    await session.flush()
    session.add(
        ArticleWorkingCopy(
            content_item_id=article.id,
            account_id=account.id,
            document=_document(),
            lock_version=4,
        )
    )
    await session.commit()

    response = await client.patch(
        f"/wechat-articles/{article.id}/working-copy",
        json={"expected_lock_version": 3, "document": _document(paragraph="Updated text.")},
        headers=await _headers(client),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ARTICLE_VERSION_CONFLICT"
    assert response.json()["error"]["details"]["currentLockVersion"] == 4


@pytest.mark.asyncio
async def test_create_article_freezes_first_draft_and_autosave_preserves_that_version(
    client, admin, session
):
    """Autosave mutates only the working copy, never its immutable first draft."""
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Article account",
    )
    session.add(account)
    await session.commit()
    headers = await _headers(client)

    created = await client.post(
        "/wechat-articles",
        headers=headers,
        json={"account_id": account.id, "document": _document()},
    )

    assert created.status_code == 201
    article_id = created.json()["articleId"]
    assert created.json()["lockVersion"] == 1
    versions = await session.execute(
        Deliverable.__table__.select().where(Deliverable.content_item_id == article_id)
    )
    first_version = versions.mappings().one()
    assert first_version["agent_code"] == "02-content-director"
    assert first_version["version"] == 1
    assert first_version["payload"]["document"] == _document()

    autosaved = await client.patch(
        f"/wechat-articles/{article_id}/working-copy",
        headers=headers,
        json={"expected_lock_version": 1, "document": _document(paragraph="Revised text.")},
    )

    assert autosaved.status_code == 200
    assert autosaved.json()["lockVersion"] == 2
    stored_version = (
        (
            await session.execute(
                Deliverable.__table__.select().where(Deliverable.content_item_id == article_id)
            )
        )
        .mappings()
        .one()
    )
    assert stored_version["payload"]["document"] == _document()


@pytest.mark.asyncio
async def test_explicit_version_diff_reports_stable_block_changes(client, admin, session):
    """The version diff must compare ArticleDocument blocks, rather than HTML."""
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Article account",
    )
    session.add(account)
    await session.commit()
    headers = await _headers(client)
    created = await client.post(
        "/wechat-articles",
        headers=headers,
        json={"account_id": account.id, "document": _document()},
    )
    article_id = created.json()["articleId"]
    changed = _document(paragraph="Revised text.")
    changed["blocks"].append({"type": "divider", "block_id": "divider-end"})
    await client.patch(
        f"/wechat-articles/{article_id}/working-copy",
        headers=headers,
        json={"expected_lock_version": 1, "document": changed},
    )

    frozen = await client.post(f"/wechat-articles/{article_id}/versions", headers=headers)
    diff = await client.get(
        f"/wechat-articles/{article_id}/versions/2/diff?base_version=1", headers=headers
    )

    assert frozen.status_code == 201
    assert frozen.json()["version"] == 2
    assert diff.status_code == 200
    assert diff.json()["changed"] == ["paragraph-intro"]
    assert diff.json()["added"] == ["divider-end"]
    assert diff.json()["removed"] == []
    assert diff.json()["moved"] == []
    assert diff.json()["textSemanticChangeRatio"] == 0.5


@pytest.mark.asyncio
async def test_freeze_returns_structured_conflict_when_unique_version_fallback_trips(
    client, admin, monkeypatch, session
):
    """A database unique race must become a safe version conflict, never an overwrite."""
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Article account",
    )
    session.add(account)
    await session.commit()
    headers = await _headers(client)
    created = await client.post(
        "/wechat-articles",
        headers=headers,
        json={"account_id": account.id, "document": _document()},
    )
    article_id = created.json()["articleId"]
    winner_session_factory = async_sessionmaker(session.bind, expire_on_commit=False)
    async with winner_session_factory() as winner_session:
        winner_session.add(
            Deliverable(
                content_item_id=article_id,
                agent_code="02-content-director",
                type=DeliverableType.WECHAT_ARTICLE,
                version=2,
                payload={"document": _document()},
                note="winning-test-version",
            )
        )
        await winner_session.commit()
    original_scalar = session.scalar
    scalar_calls = 0

    async def stale_first_version_read(statement, *args, **kwargs):
        nonlocal scalar_calls
        scalar_calls += 1
        if scalar_calls == 1:
            return 1
        return await original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", stale_first_version_read)

    async def raise_unique_conflict() -> None:
        raise IntegrityError("INSERT", {}, Exception("simulated concurrent unique conflict"))

    monkeypatch.setattr(session, "flush", raise_unique_conflict)
    response = await client.post(f"/wechat-articles/{article_id}/versions", headers=headers)

    assert scalar_calls == 2
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "ARTICLE_VERSION_CONFLICT",
        "details": {"currentVersion": 2},
    }


@pytest.mark.asyncio
async def test_inaccessible_article_and_preview_contract_fail_closed(
    client, admin, member, session
):
    """Article reads must hide inaccessible account lineage and preview must not render HTML."""
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Hidden article account",
    )
    session.add(account)
    await session.commit()
    admin_headers = await _headers(client)
    created = await client.post(
        "/wechat-articles",
        headers=admin_headers,
        json={"account_id": account.id, "document": _document()},
    )
    article_id = created.json()["articleId"]
    member_login = await client.post(
        "/auth/login", json={"email": "user@test.com", "password": "user-pw-123"}
    )
    member_headers = {"Authorization": f"Bearer {member_login.json()['access_token']}"}

    hidden = await client.get(f"/wechat-articles/{article_id}/working-copy", headers=member_headers)
    preview = await client.get(f"/wechat-articles/{article_id}/preview", headers=admin_headers)

    assert hidden.status_code == 404
    assert preview.status_code == 200
    assert preview.json()["renderedHtml"] is None
    assert preview.json()["document"] == _document()


@pytest.mark.asyncio
async def test_article_version_service_entries_freeze_rewrite_presync_and_successful_sync(
    client, admin, session
):
    """Every non-autosave article milestone appends an immutable snapshot."""
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Article account",
    )
    session.add(account)
    await session.commit()
    headers = await _headers(client)
    created = await client.post(
        "/wechat-articles",
        headers=headers,
        json={"account_id": account.id, "document": _document()},
    )
    article_id = created.json()["articleId"]

    rewritten = await snapshot_completed_whole_article_rewrite(
        session, admin, content_item_id=article_id
    )
    pre_sync = await freeze_before_sync(session, admin, content_item_id=article_id)
    synced = await snapshot_successful_sync(session, admin, content_item_id=article_id)

    assert [rewritten.version, pre_sync.version, synced.version] == [2, 3, 4]
    assert [
        rewritten.note,
        pre_sync.note,
        synced.note,
    ] == [
        "article_version:whole_article_ai_rewrite",
        "article_version:pre_sync_freeze",
        "article_version:successful_sync_snapshot",
    ]


@pytest.mark.asyncio
async def test_freeze_revalidates_malformed_working_copy_before_persisting_version(
    client, admin, session
):
    """An invalid source document must not become an immutable Deliverable payload."""
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Article account",
    )
    session.add(account)
    await session.commit()
    headers = await _headers(client)
    created = await client.post(
        "/wechat-articles",
        headers=headers,
        json={"account_id": account.id, "document": _document()},
    )
    article_id = created.json()["articleId"]
    working_copy = await session.scalar(
        select(ArticleWorkingCopy).where(ArticleWorkingCopy.content_item_id == article_id)
    )
    assert working_copy is not None
    set_committed_value(
        working_copy,
        "document",
        {
            **_document(),
            "blocks": [{"type": "rawHtml", "block_id": "unsafe", "html": "<script>bad()</script>"}],
        },
    )

    with pytest.raises(ValidationError):
        await freeze_article_version(
            session,
            admin,
            content_item_id=article_id,
            trigger="explicit_save_version",
        )

    version_count = await session.scalar(
        select(func.count(Deliverable.id)).where(Deliverable.content_item_id == article_id)
    )
    assert version_count == 1


@pytest.mark.asyncio
async def test_create_revalidates_malformed_source_before_persisting_first_version(admin, session):
    """A bypassed source object cannot create an invalid immutable first draft."""
    account = Account(
        org_id=admin.org_id,
        platform=Platform.WECHAT_OFFICIAL_ACCOUNT,
        nickname="Article account",
    )
    session.add(account)
    await session.commit()
    malformed_document = cast(
        ArticleDocument,
        {
            **_document(),
            "blocks": [{"type": "rawHtml", "block_id": "unsafe", "html": "<script>bad()</script>"}],
        },
    )

    with pytest.raises(ValidationError):
        await create_article(session, admin, account_id=account.id, document=malformed_document)

    assert await session.scalar(select(func.count(ContentItem.id))) == 0
