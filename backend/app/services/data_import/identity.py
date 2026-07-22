from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PlatformContentRecord
from app.models.enums import ContentIdentityConfidence, Platform


@dataclass(frozen=True, slots=True)
class ContentMatch:
    confidence: ContentIdentityConfidence
    matched_content_id: int | None = None
    candidate_content_ids: list[int] = field(default_factory=list)
    weak_fingerprint: str | None = None


async def match_content(
    session: AsyncSession,
    *,
    account_id: int,
    platform: Platform,
    normalized_row: dict[str, Any],
) -> ContentMatch:
    external_content_id = _normalize_identity_text(normalized_row.get("external_content_id"))
    if external_content_id is not None:
        record = await session.scalar(
            select(PlatformContentRecord).where(
                PlatformContentRecord.account_id == account_id,
                PlatformContentRecord.platform == platform,
                PlatformContentRecord.external_content_id == external_content_id,
            )
        )
        if record is not None:
            return ContentMatch(
                confidence=ContentIdentityConfidence.CONFIRMED,
                matched_content_id=record.id,
                candidate_content_ids=[record.id],
            )

    share_url = canonicalize_share_url(normalized_row.get("share_url"))
    if share_url is not None:
        records = (
            await session.scalars(
                select(PlatformContentRecord).where(
                    PlatformContentRecord.account_id == account_id,
                    PlatformContentRecord.platform == platform,
                    PlatformContentRecord.share_url.is_not(None),
                )
            )
        ).all()
        for record in records:
            if canonicalize_share_url(record.share_url) == share_url:
                return ContentMatch(
                    confidence=ContentIdentityConfidence.CONFIRMED,
                    matched_content_id=record.id,
                    candidate_content_ids=[record.id],
                )

    title = _normalize_identity_text(normalized_row.get("title"))
    published_at = _coerce_datetime(normalized_row.get("published_at"))
    weak_fingerprint = _build_weak_fingerprint(title=title, published_at=published_at)
    if title is not None and published_at is not None:
        records = (
            await session.scalars(
                select(PlatformContentRecord).where(
                    PlatformContentRecord.account_id == account_id,
                    PlatformContentRecord.platform == platform,
                    PlatformContentRecord.published_at == published_at,
                    PlatformContentRecord.title.is_not(None),
                )
            )
        ).all()
        candidate_ids = sorted(
            record.id
            for record in records
            if _normalize_identity_text(record.title) == title
        )
        if len(candidate_ids) == 1:
            return ContentMatch(
                confidence=ContentIdentityConfidence.PROVISIONAL,
                candidate_content_ids=candidate_ids,
                weak_fingerprint=weak_fingerprint,
            )
        if len(candidate_ids) > 1:
            return ContentMatch(
                confidence=ContentIdentityConfidence.AMBIGUOUS,
                candidate_content_ids=candidate_ids,
                weak_fingerprint=weak_fingerprint,
            )

    return ContentMatch(
        confidence=ContentIdentityConfidence.UNRESOLVED,
        weak_fingerprint=weak_fingerprint,
    )


def canonicalize_share_url(value: Any) -> str | None:
    text = _normalize_identity_text(value)
    if text is None:
        return None
    parts = urlsplit(text)
    if not parts.scheme or not parts.netloc:
        return text
    hostname = parts.hostname.lower() if parts.hostname else ""
    port = parts.port
    netloc = hostname
    if port is not None and not _is_default_port(parts.scheme, port):
        netloc = f"{hostname}:{port}"
    path = parts.path or ""
    if path != "/":
        path = path.rstrip("/")
    canonical = SplitResult(
        scheme=parts.scheme.lower(),
        netloc=netloc,
        path=path,
        query="",
        fragment="",
    )
    return urlunsplit(canonical)


def normalize_title(value: Any) -> str | None:
    return _normalize_identity_text(value)


def coerce_published_at(value: Any) -> datetime | None:
    return _coerce_datetime(value)


def build_weak_fingerprint(*, title: Any, published_at: Any) -> str | None:
    return _build_weak_fingerprint(
        title=_normalize_identity_text(title),
        published_at=_coerce_datetime(published_at),
    )


def _normalize_identity_text(value: Any) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    normalized = " ".join(text.split())
    return normalized or None


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = _normalize_identity_text(value)
    if text is None:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _build_weak_fingerprint(*, title: str | None, published_at: datetime | None) -> str | None:
    if title is None or published_at is None:
        return None
    return f"{published_at.isoformat()}::{title}"


def _is_default_port(scheme: str, port: int) -> bool:
    return (scheme.lower() == "http" and port == 80) or (
        scheme.lower() == "https" and port == 443
    )
