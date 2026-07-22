from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

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
        records = (
            await session.scalars(
                select(PlatformContentRecord).where(
                    PlatformContentRecord.account_id == account_id,
                    PlatformContentRecord.platform == platform,
                    PlatformContentRecord.external_content_id == external_content_id,
                )
            )
        ).all()
        if len(records) == 1:
            return ContentMatch(
                confidence=ContentIdentityConfidence.CONFIRMED,
                matched_content_id=records[0].id,
                candidate_content_ids=[records[0].id],
            )
        if len(records) > 1:
            return ContentMatch(
                confidence=ContentIdentityConfidence.AMBIGUOUS,
                candidate_content_ids=sorted(record.id for record in records),
            )

    canonical_share_url = canonicalize_share_url(normalized_row.get("share_url"))
    if canonical_share_url is not None:
        records = (
            await session.scalars(
                select(PlatformContentRecord).where(
                    PlatformContentRecord.account_id == account_id,
                    PlatformContentRecord.platform == platform,
                    PlatformContentRecord.canonical_share_url == canonical_share_url,
                )
            )
        ).all()
        if len(records) == 1:
            return ContentMatch(
                confidence=ContentIdentityConfidence.CONFIRMED,
                matched_content_id=records[0].id,
                candidate_content_ids=[records[0].id],
            )
        if len(records) > 1:
            return ContentMatch(
                confidence=ContentIdentityConfidence.AMBIGUOUS,
                candidate_content_ids=sorted(record.id for record in records),
            )

    title = _normalize_identity_text(normalized_row.get("title"))
    published_at = _normalize_identity_datetime(normalized_row.get("published_at"))
    weak_fingerprint = _build_weak_fingerprint(title=title, published_at=published_at)
    if title is not None and published_at is not None:
        records = (
            await session.scalars(
                select(PlatformContentRecord).where(
                    PlatformContentRecord.account_id == account_id,
                    PlatformContentRecord.platform == platform,
                    PlatformContentRecord.title.is_not(None),
                    PlatformContentRecord.published_at.is_not(None),
                )
            )
        ).all()
        candidate_ids = sorted(
            record.id
            for record in records
            if _normalize_identity_text(record.title) == title
            and _normalize_identity_datetime(record.published_at) == published_at
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

    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    if not parts.netloc or parts.username or parts.password or parts.hostname is None:
        return None

    try:
        port = parts.port
    except ValueError:
        return None

    hostname = parts.hostname.encode("idna").decode("ascii").lower()
    netloc = hostname
    if port is not None and not _is_default_port(parts.scheme, port):
        netloc = f"{hostname}:{port}"

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    query = urlencode(sorted(query_pairs), doseq=True)
    canonical = SplitResult(
        scheme=parts.scheme.lower(),
        netloc=netloc,
        path=path,
        query=query,
        fragment="",
    )
    return urlunsplit(canonical)


def normalize_title(value: Any) -> str | None:
    return _normalize_identity_text(value)


def coerce_published_at(value: Any) -> datetime | None:
    return _normalize_identity_datetime(value)


def build_weak_fingerprint(*, title: Any, published_at: Any) -> str | None:
    return _build_weak_fingerprint(
        title=_normalize_identity_text(title),
        published_at=_normalize_identity_datetime(published_at),
    )


def _normalize_identity_text(value: Any) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    normalized = " ".join(text.split())
    return normalized or None


def _normalize_identity_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = _normalize_identity_text(value)
        if text is None:
            return None
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            try:
                dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _build_weak_fingerprint(*, title: str | None, published_at: datetime | None) -> str | None:
    if title is None or published_at is None:
        return None
    return f"{published_at.isoformat()}::{title}"


def _is_default_port(scheme: str, port: int) -> bool:
    return (scheme.lower() == "http" and port == 80) or (
        scheme.lower() == "https" and port == 443
    )
