from __future__ import annotations

from datetime import datetime

import pytest

from app.models import Account, PlatformContentRecord
from app.models.enums import ContentIdentityConfidence, Platform
from app.services.data_import.identity import match_content


@pytest.fixture
async def account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Identity fixture",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@pytest.fixture
async def other_account(session, admin) -> Account:
    item = Account(
        org_id=admin.org_id,
        platform=Platform.DOUYIN,
        nickname="Identity other fixture",
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@pytest.mark.asyncio
async def test_external_content_id_match_is_strong_and_takes_priority(session, admin, account):
    strong = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        external_content_id="7299001",
        share_url="https://www.douyin.com/video/7299001",
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    same_title = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        external_content_id="7299002",
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add_all([strong, same_title])
    await session.commit()

    result = await match_content(
        session,
        account_id=account.id,
        platform=account.platform,
        normalized_row={
            "external_content_id": "7299001",
            "share_url": "https://www.douyin.com/video/7299002",
            "title": "作品 A",
            "published_at": "2026-07-18T14:11:20",
        },
    )

    assert result.confidence is ContentIdentityConfidence.CONFIRMED
    assert result.matched_content_id == strong.id
    assert result.candidate_content_ids == [strong.id]


@pytest.mark.asyncio
async def test_share_url_match_uses_canonicalized_url(session, admin, account):
    content = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        share_url="https://www.douyin.com/video/7299001/",
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(content)
    await session.commit()

    result = await match_content(
        session,
        account_id=account.id,
        platform=account.platform,
        normalized_row={"share_url": " HTTPS://www.douyin.com/video/7299001?foo=1#bar "},
    )

    assert result.confidence is ContentIdentityConfidence.CONFIRMED
    assert result.matched_content_id == content.id
    assert result.candidate_content_ids == [content.id]


@pytest.mark.asyncio
async def test_title_and_publish_time_create_only_provisional_match(session, admin, account):
    content = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(content)
    await session.commit()

    result = await match_content(
        session,
        account_id=account.id,
        platform=account.platform,
        normalized_row={
            "title": "  作品　A  ",
            "published_at": "2026-07-18T14:11:20",
        },
    )

    assert result.confidence is ContentIdentityConfidence.PROVISIONAL
    assert result.matched_content_id is None
    assert result.candidate_content_ids == [content.id]


@pytest.mark.asyncio
async def test_multiple_provisional_candidates_are_ambiguous(session, admin, account):
    first = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    second = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=account.id,
        platform=Platform.DOUYIN,
        title="作品　A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add_all([first, second])
    await session.commit()

    result = await match_content(
        session,
        account_id=account.id,
        platform=account.platform,
        normalized_row={
            "title": "作品 A",
            "published_at": "2026-07-18 14:11:20",
        },
    )

    assert result.confidence is ContentIdentityConfidence.AMBIGUOUS
    assert result.matched_content_id is None
    assert result.candidate_content_ids == [first.id, second.id]


@pytest.mark.asyncio
async def test_matching_does_not_cross_account_boundaries(session, admin, account, other_account):
    content = PlatformContentRecord(
        org_id=admin.org_id,
        account_id=other_account.id,
        platform=Platform.DOUYIN,
        external_content_id="7299001",
        share_url="https://www.douyin.com/video/7299001",
        title="作品 A",
        published_at=datetime(2026, 7, 18, 14, 11, 20),
        identity_confidence=ContentIdentityConfidence.CONFIRMED,
    )
    session.add(content)
    await session.commit()

    result = await match_content(
        session,
        account_id=account.id,
        platform=account.platform,
        normalized_row={
            "external_content_id": "7299001",
            "share_url": "https://www.douyin.com/video/7299001",
            "title": "作品 A",
            "published_at": "2026-07-18T14:11:20",
        },
    )

    assert result.confidence is ContentIdentityConfidence.UNRESOLVED
    assert result.matched_content_id is None
    assert result.candidate_content_ids == []
