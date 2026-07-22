from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook

from app.services.data_import.templates import (
    ColumnDefinition,
    TemplateDefinition,
    detect_template,
    normalize_header_value,
)

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_DATA_ROWS = 10_000
MAX_COLUMNS = 100
SUPPORTED_EXTENSIONS = {".xlsx", ".csv"}
MISSING_MARKERS = {"", "-", "--", "N/A", "n/a", "null", "None"}
TOO_MANY_COLUMNS_MESSAGE = "Files with more than 100 columns are not supported"
TOO_MANY_ROWS_MESSAGE = "Files with more than 10,000 rows are not supported"


class ParseFailure(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RowIssue:
    code: str
    message: str
    field: str | None = None


@dataclass(slots=True)
class NormalizedRow:
    template_code: str
    row_number: int
    raw: dict[str, Any]
    normalized: dict[str, Any]
    errors: list[RowIssue] = field(default_factory=list)
    warnings: list[RowIssue] = field(default_factory=list)


ValidatedRow = NormalizedRow


@dataclass(frozen=True, slots=True)
class ImportPreview:
    template_code: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    warning_rows: int


@dataclass(frozen=True, slots=True)
class ParsedDataset:
    template_code: str
    template_name: str
    headers: list[str]
    rows: list[ValidatedRow]
    preview: ImportPreview


def parse_source_file(filename: str, data: bytes) -> ParsedDataset:
    extension = _validate_source(filename, data)
    headers, raw_rows = _parse_xlsx(data) if extension == ".xlsx" else _parse_csv(data)
    template = _detect_template_or_fail(headers)
    rows = _normalize_rows(template, raw_rows)
    preview = build_preview(rows)
    return ParsedDataset(
        template_code=template.code,
        template_name=template.display_name,
        headers=headers,
        rows=rows,
        preview=preview,
    )


def build_preview(rows: list[ValidatedRow]) -> ImportPreview:
    template_code = rows[0].template_code if rows else ""
    invalid_rows = sum(1 for row in rows if row.errors)
    warning_rows = sum(1 for row in rows if row.warnings)
    return ImportPreview(
        template_code=template_code,
        total_rows=len(rows),
        valid_rows=len(rows) - invalid_rows,
        invalid_rows=invalid_rows,
        warning_rows=warning_rows,
    )


def _validate_source(filename: str, data: bytes) -> str:
    if "\x00" in filename:
        raise ParseFailure(
            "invalid_filename",
            "Embedded NUL bytes are not allowed in filenames",
        )
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ParseFailure("unsupported_extension", "Unsupported file extension")
    if len(data) > MAX_FILE_BYTES:
        raise ParseFailure("file_too_large", "Files larger than 10 MB are not supported")
    return extension


def _parse_csv(data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    text = _decode_csv(data)
    stream = io.StringIO(text, newline="")
    try:
        reader = csv.reader(stream, strict=True)
        rows = list(reader)
    except csv.Error as exc:
        raise ParseFailure("malformed_csv", f"Malformed CSV: {exc}") from exc
    return _rows_to_table(rows)


def _decode_csv(data: bytes) -> str:
    try:
        if data.startswith(b"\xef\xbb\xbf"):
            text = data.decode("utf-8-sig")
        else:
            text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseFailure("invalid_encoding", "CSV files must use UTF-8 or UTF-8 BOM") from exc
    if "\x00" in text:
        raise ParseFailure("embedded_nul", "Embedded NUL bytes are not allowed")
    return text


def _parse_xlsx(data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    _scan_xlsx_archive(data)
    try:
        workbook = load_workbook(
            filename=io.BytesIO(data),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
    except BadZipFile as exc:
        raise ParseFailure("invalid_xlsx", "The workbook is not a valid XLSX file") from exc
    except Exception as exc:  # pragma: no cover
        raise ParseFailure("invalid_xlsx", f"Unable to read workbook: {exc}") from exc

    worksheet = workbook.worksheets[0] if workbook.worksheets else None
    if worksheet is None:
        raise ParseFailure("empty_workbook", "The workbook does not contain any worksheets")

    rows: list[list[Any]] = []
    for row in worksheet.iter_rows(values_only=True):
        values = list(row)
        last_index = max(
            (
                index
                for index, value in enumerate(values)
                if value is not None and str(value).strip() != ""
            ),
            default=-1,
        )
        if last_index < 0:
            continue
        trimmed = values[: last_index + 1]
        if len(trimmed) > MAX_COLUMNS:
            raise ParseFailure("too_many_columns", TOO_MANY_COLUMNS_MESSAGE)
        rows.append(trimmed)
        if len(rows) - 1 > MAX_DATA_ROWS:
            raise ParseFailure("too_many_rows", TOO_MANY_ROWS_MESSAGE)

    return _rows_to_table(rows)


def _scan_xlsx_archive(data: bytes) -> None:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if any(name.startswith("xl/externalLinks/") for name in names):
                raise ParseFailure(
                    "external_links_unsupported",
                    "Workbooks with external links are not supported",
                )
            if any(name.endswith("vbaProject.bin") for name in names):
                raise ParseFailure(
                    "macros_unsupported",
                    "Macro-enabled workbooks are not supported",
                )
            for name in names:
                if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
                    continue
                if b"<f" in archive.read(name):
                    raise ParseFailure(
                        "formula_cells_unsupported",
                        "Formula cells are not supported",
                    )
    except BadZipFile as exc:
        raise ParseFailure("invalid_xlsx", "The workbook is not a valid XLSX file") from exc


def _rows_to_table(rows: list[list[Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    if not rows:
        raise ParseFailure("empty_file", "The file does not contain any rows")
    headers = [_stringify_header(value) for value in rows[0]]
    _validate_headers(headers)
    data_rows: list[dict[str, Any]] = []
    for row_number, values in enumerate(rows[1:], start=2):
        trimmed = list(values[: len(headers)])
        if len(trimmed) < len(headers):
            trimmed.extend([None] * (len(headers) - len(trimmed)))
        if _row_is_blank(trimmed):
            continue
        data_rows.append({"row_number": row_number, "values": trimmed})
        if len(data_rows) > MAX_DATA_ROWS:
            raise ParseFailure("too_many_rows", TOO_MANY_ROWS_MESSAGE)
    return headers, data_rows


def _stringify_header(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\ufeff", "").strip()


def _validate_headers(headers: list[str]) -> None:
    if len(headers) > MAX_COLUMNS:
        raise ParseFailure("too_many_columns", TOO_MANY_COLUMNS_MESSAGE)
    normalized = [normalize_header_value(header) for header in headers]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for header in normalized:
        if not header:
            continue
        if header in seen:
            duplicates.add(header)
        seen.add(header)
    if duplicates:
        raise ParseFailure("duplicate_headers", "Duplicate headers are not allowed")
    if any(not header for header in normalized):
        raise ParseFailure("blank_headers", "Blank headers are not supported")


def _detect_template_or_fail(headers: list[str]) -> TemplateDefinition:
    try:
        return detect_template(headers)
    except ValueError as exc:
        if str(exc) == "ambiguous":
            raise ParseFailure("ambiguous_template", "Ambiguous template signature") from exc
        raise ParseFailure("unknown_template", "Unknown or unsupported template") from exc


def _normalize_rows(
    template: TemplateDefinition,
    raw_rows: list[dict[str, Any]],
) -> list[ValidatedRow]:
    rows: list[ValidatedRow] = []
    for row in raw_rows:
        row_values = row["values"]
        raw = {
            column.canonical_header: row_values[index] if index < len(row_values) else None
            for index, column in enumerate(template.columns)
        }
        normalized: dict[str, Any] = {}
        errors: list[RowIssue] = []
        warnings: list[RowIssue] = []
        for column in template.columns:
            parsed_value, issues = _parse_column_value(column, raw[column.canonical_header])
            normalized.update(parsed_value)
            errors.extend(issues)
        rows.append(
            ValidatedRow(
                template_code=template.code,
                row_number=row["row_number"],
                raw=raw,
                normalized=normalized,
                errors=errors,
                warnings=warnings,
            )
        )
    return rows


def _parse_column_value(
    column: ColumnDefinition,
    raw_value: Any,
) -> tuple[dict[str, Any], list[RowIssue]]:
    field_name = column.field_name
    if column.value_type == "string":
        return {field_name: _normalize_string(raw_value)}, []
    if column.value_type == "int":
        return _parse_int(field_name, raw_value)
    if column.value_type == "float":
        return _parse_float(field_name, raw_value)
    if column.value_type == "ratio":
        return _parse_ratio(field_name, raw_value)
    if column.value_type == "date":
        return _parse_date(field_name, raw_value)
    if column.value_type == "datetime":
        return _parse_datetime(field_name, raw_value)
    if column.value_type == "date_range":
        return _parse_date_range(raw_value)
    raise ParseFailure(
        "unsupported_column_type",
        f"Unsupported column type: {column.value_type}",
    )


def _normalize_string(value: Any) -> str | None:
    text = _coerce_text(value)
    return None if text is None else text.strip()


def _parse_int(field_name: str, value: Any) -> tuple[dict[str, Any], list[RowIssue]]:
    text = _coerce_text(value)
    if text is None:
        return {field_name: None}, []
    try:
        number = float(text)
    except ValueError:
        return {field_name: None}, [_issue("invalid_integer", field_name)]
    if not number.is_integer():
        return {field_name: None}, [_issue("invalid_integer", field_name)]
    return {field_name: int(number)}, []


def _parse_float(field_name: str, value: Any) -> tuple[dict[str, Any], list[RowIssue]]:
    text = _coerce_text(value)
    if text is None:
        return {field_name: None}, []
    try:
        return {field_name: float(text)}, []
    except ValueError:
        return {field_name: None}, [_issue("invalid_number", field_name)]


def _parse_ratio(field_name: str, value: Any) -> tuple[dict[str, Any], list[RowIssue]]:
    text = _coerce_text(value)
    if text is None:
        return {field_name: None}, []
    try:
        number = float(text[:-1]) / 100 if text.endswith("%") else float(text)
    except ValueError:
        return {field_name: None}, [_issue("invalid_number", field_name)]
    if not 0 <= number <= 1:
        return {field_name: None}, [_issue("out_of_range", field_name)]
    return {field_name: number}, []


def _parse_date(field_name: str, value: Any) -> tuple[dict[str, Any], list[RowIssue]]:
    text = _coerce_text(value)
    if text is None:
        return {field_name: None}, []
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return {field_name: datetime.strptime(text, fmt).date()}, []
        except ValueError:
            continue
    return {field_name: None}, [_issue("invalid_date", field_name)]


def _parse_datetime(field_name: str, value: Any) -> tuple[dict[str, Any], list[RowIssue]]:
    text = _coerce_text(value)
    if text is None:
        return {field_name: None}, []
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return {field_name: datetime.strptime(text, fmt)}, []
        except ValueError:
            continue
    return {field_name: None}, [_issue("invalid_datetime", field_name)]


def _parse_date_range(value: Any) -> tuple[dict[str, Any], list[RowIssue]]:
    text = _coerce_text(value)
    if text is None:
        return {"period_start": None, "period_end": None}, []
    separator = " ~ " if " ~ " in text else "~"
    parts = [part.strip() for part in text.split(separator)]
    if len(parts) != 2:
        return (
            {"period_start": None, "period_end": None},
            [RowIssue(code="invalid_date_range", message="Invalid date range", field="period")],
        )
    start_value, start_errors = _parse_date("period_start", parts[0])
    end_value, end_errors = _parse_date("period_end", parts[1])
    return {**start_value, **end_value}, [*start_errors, *end_errors]


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if text in MISSING_MARKERS:
        return None
    return text


def _row_is_blank(values: list[Any]) -> bool:
    return all(_coerce_text(value) is None for value in values)


def _issue(code: str, field_name: str) -> RowIssue:
    messages = {
        "invalid_integer": f"Invalid integer for {field_name}",
        "invalid_number": f"Invalid number for {field_name}",
        "out_of_range": f"{field_name} must be between 0 and 1",
        "invalid_date": f"Invalid date for {field_name}",
        "invalid_datetime": f"Invalid datetime for {field_name}",
    }
    return RowIssue(code=code, message=messages[code], field=field_name)
