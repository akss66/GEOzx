from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict

from app.models.enums import DataSourceKind
from app.services.data_import.parser import (
    ParsedDataset,
    ValidatedRow,
    build_preview,
    parse_single_source_file,
)


class SourceInput(TypedDict):
    filename: str
    data: bytes


@dataclass(frozen=True, slots=True)
class DetectionResult:
    matched: bool
    source_kind: DataSourceKind
    reason: str | None = None


class DataSourceAdapter(Protocol):
    source_kind: DataSourceKind

    def detect(self, source: SourceInput) -> DetectionResult: ...

    def parse(self, source: SourceInput) -> ParsedDataset: ...

    def normalize(self, parsed: ParsedDataset) -> list[ValidatedRow]: ...

    def validate(self, rows: list[ValidatedRow]) -> list[ValidatedRow]: ...

    def preview(
        self,
        rows: list[ValidatedRow],
        *,
        template_code: str | None = None,
    ): ...


@dataclass(slots=True)
class FileDataSourceAdapter:
    source_kind: DataSourceKind = DataSourceKind.PLATFORM_EXPORT

    def detect(self, source: SourceInput) -> DetectionResult:
        filename = source["filename"].lower()
        if filename.endswith(".xlsx") or filename.endswith(".csv"):
            return DetectionResult(matched=True, source_kind=self.source_kind)
        return DetectionResult(
            matched=False,
            source_kind=self.source_kind,
            reason="unsupported_extension",
        )

    def parse(self, source: SourceInput) -> ParsedDataset:
        return parse_single_source_file(source["filename"], source["data"])

    def normalize(self, parsed: ParsedDataset) -> list[ValidatedRow]:
        return parsed.rows

    def validate(self, rows: list[ValidatedRow]) -> list[ValidatedRow]:
        return rows

    def preview(
        self,
        rows: list[ValidatedRow],
        *,
        template_code: str | None = None,
    ):
        return build_preview(rows, template_code=template_code)


REGISTERED_ADAPTERS: tuple[DataSourceAdapter, ...] = (FileDataSourceAdapter(),)
