import pytest

from app.services.data_import.parser import ParseFailure, parse_source_file
from tests.test_data_import_templates import (
    DAILY_HEADERS,
    SINGLE_CONTENT_HEADERS,
    WORK_LIST_HEADERS,
    csv_bytes,
)


def test_reordered_columns_are_normalized_by_header_instead_of_position():
    headers = list(reversed(WORK_LIST_HEADERS))
    original_values = [
        "作品 A",
        "2026-07-18 14:11:20",
        "1min-视频",
        "公开",
        "81",
        "0.0875",
        "0.375",
        "0.12",
        "0.25",
        "9.53",
        "6",
        "1",
        "3",
        "2",
        "4",
        "0",
    ]
    values_by_header = dict(zip(WORK_LIST_HEADERS, original_values, strict=True))

    parsed = parse_source_file(
        "works.csv",
        csv_bytes(headers, [[values_by_header[header] for header in headers]]),
    )

    assert parsed.template_code == "douyin_work_list_v1"
    assert parsed.rows[0].normalized["title"] == "作品 A"
    assert parsed.rows[0].normalized["play"] == 81
    assert parsed.rows[0].normalized["comment_count"] == 3


def test_optional_columns_can_be_absent_when_required_signature_is_present():
    required_headers = SINGLE_CONTENT_HEADERS[:3]

    parsed = parse_source_file(
        "single.csv",
        csv_bytes(
            required_headers,
            [["作品 A", "2026-07-18 14:11:20", "81"]],
        ),
    )

    assert parsed.template_code == "douyin_single_content_v1"
    assert parsed.rows[0].errors == []
    assert parsed.rows[0].normalized["play"] == 81
    assert parsed.rows[0].normalized["completion_rate_5s"] is None


def test_unrelated_extra_columns_are_preserved_in_raw_audit_and_warned():
    headers = [*DAILY_HEADERS, "来源备注"]

    parsed = parse_source_file(
        "daily.csv",
        csv_bytes(headers, [["2026-07-18", "81", "后台导出"]]),
    )

    row = parsed.rows[0]
    assert parsed.template_code == "douyin_daily_play_v1"
    assert row.normalized["play"] == 81
    assert row.raw["来源备注"] == "后台导出"
    assert [(warning.code, warning.field) for warning in row.warnings] == [
        ("ignored_column", "来源备注")
    ]


def test_two_alias_headers_for_one_field_are_rejected_as_ambiguous():
    headers = ["视频名称", "作品名称", "发布时间", "播放量"]

    with pytest.raises(ParseFailure, match="Multiple columns map to the same field"):
        parse_source_file(
            "ambiguous.csv",
            csv_bytes(
                headers,
                [["作品 A", "作品 A", "2026-07-18 14:11:20", "81"]],
            ),
        )


def test_missing_required_headers_does_not_false_positive_as_a_template():
    with pytest.raises(ParseFailure, match="Unknown or unsupported template"):
        parse_source_file(
            "incomplete.csv",
            csv_bytes(["日期", "来源备注"], [["2026-07-18", "后台导出"]]),
        )
