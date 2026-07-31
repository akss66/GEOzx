"""Pure merge rules for account-data observations."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.models.enums import DataSourceKind

_SOURCE_PRIORITIES: dict[DataSourceKind, int] = {
    DataSourceKind.OFFICIAL_API: 400,
    DataSourceKind.PLATFORM_EXPORT: 300,
    DataSourceKind.SCREENSHOT_VERIFIED: 200,
    DataSourceKind.MANUAL_ENTRY: 100,
}
_MISSING_TEXT_MARKERS = {"", "-", "--", "n/a", "null", "none"}


@dataclass(frozen=True, slots=True)
class MergeCandidate:
    value: Any
    source_kind: DataSourceKind
    confirmed_sequence: int
    observation_id: int
    source_priority: int | None = None
    active: bool = True


def _segment(value: object) -> str:
    return quote(str(value), safe="")


def account_entity_key(account_id: int) -> str:
    return f"account:{account_id}"


def content_entity_key(account_id: int, platform_content_record_id: int) -> str:
    return f"account:{account_id}:content:{platform_content_record_id}"


def audience_entity_key(account_id: int, dimension: str, label: str) -> str:
    return (
        f"account:{account_id}:audience:"
        f"{_segment(dimension)}:{_segment(label)}"
    )


def benchmark_entity_key(account_id: int, benchmark_code: str) -> str:
    return f"account:{account_id}:benchmark:{_segment(benchmark_code)}"


def iter_present_fields(normalized: Mapping[str, Any]) -> Iterator[tuple[str, Any]]:
    for field_name, value in normalized.items():
        if value is None:
            continue
        if isinstance(value, str) and value.strip().casefold() in _MISSING_TEXT_MARKERS:
            continue
        yield field_name, value


def source_priority(source_kind: DataSourceKind) -> int:
    return _SOURCE_PRIORITIES[source_kind]


def choose_winner(candidates: Iterable[MergeCandidate]) -> MergeCandidate | None:
    active_candidates = (candidate for candidate in candidates if candidate.active)
    return max(
        active_candidates,
        key=lambda candidate: (
            (
                candidate.source_priority
                if candidate.source_priority is not None
                else source_priority(candidate.source_kind)
            ),
            candidate.confirmed_sequence,
            candidate.observation_id,
        ),
        default=None,
    )
