import io

import pytest
from openpyxl import Workbook

import app.services.data_import.parser as parser_module
from app.services.data_import.parser import ParseFailure, parse_source_file
from tests.test_data_import_templates import DAILY_HEADERS, WORK_LIST_HEADERS


def _workbook_with_sheets(
    sheets: list[tuple[str, list[str] | None, list[list[object]]]],
) -> bytes:
    workbook = Workbook()
    default = workbook.active
    workbook.remove(default)
    for name, headers, rows in sheets:
        worksheet = workbook.create_sheet(name)
        if headers is not None:
            worksheet.append(headers)
        for row in rows:
            worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def _work_list_row(title: str) -> list[object]:
    return [
        title,
        "2026-07-18 14:11:20",
        "1min-视频",
        "公开",
        81,
        0.0875,
        0.375,
        0.12,
        0.25,
        9.53,
        6,
        1,
        3,
        2,
        4,
        0,
    ]


def test_one_workbook_yields_every_supported_worksheet_as_a_dataset():
    parsed = parse_source_file(
        "account-data.xlsx",
        _workbook_with_sheets(
            [
                ("每日播放", DAILY_HEADERS, [["2026-07-18", 81]]),
                ("作品列表", WORK_LIST_HEADERS, [_work_list_row("作品 A")]),
            ]
        ),
    )

    assert [dataset.template_code for dataset in parsed.datasets] == [
        "douyin_daily_play_v1",
        "douyin_work_list_v1",
    ]
    assert [dataset.sheet_name for dataset in parsed.datasets] == ["每日播放", "作品列表"]
    assert [dataset.dataset_ordinal for dataset in parsed.datasets] == [1, 2]


def test_blank_worksheet_is_skipped_without_blocking_supported_sibling():
    parsed = parse_source_file(
        "account-data.xlsx",
        _workbook_with_sheets(
            [
                ("说明", None, []),
                ("每日播放", DAILY_HEADERS, [["2026-07-18", 81]]),
            ]
        ),
    )

    assert [dataset.template_code for dataset in parsed.datasets] == [
        "douyin_daily_play_v1"
    ]
    assert [(warning.code, warning.field) for warning in parsed.warnings] == [
        ("blank_worksheet_skipped", "说明")
    ]


def test_unknown_worksheet_is_isolated_from_supported_sibling():
    parsed = parse_source_file(
        "account-data.xlsx",
        _workbook_with_sheets(
            [
                ("每日播放", DAILY_HEADERS, [["2026-07-18", 81]]),
                ("未知表", ["抖音号", "备注"], [["account", "note"]]),
            ]
        ),
    )

    assert [dataset.template_code for dataset in parsed.datasets] == [
        "douyin_daily_play_v1"
    ]
    assert [(failure.sheet_name, failure.code) for failure in parsed.failures] == [
        ("未知表", "unknown_template")
    ]


def test_normalized_duplicate_worksheet_names_receive_stable_suffixes():
    parsed = parse_source_file(
        "account-data.xlsx",
        _workbook_with_sheets(
            [
                ("Data", DAILY_HEADERS, [["2026-07-18", 81]]),
                (" Data ", DAILY_HEADERS, [["2026-07-19", 82]]),
            ]
        ),
    )

    assert [dataset.sheet_name for dataset in parsed.datasets] == ["Data", "Data (2)"]


def test_row_limit_is_enforced_across_all_worksheets(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(parser_module, "MAX_DATA_ROWS", 2)
    payload = _workbook_with_sheets(
        [
            (
                "First",
                DAILY_HEADERS,
                [["2026-07-18", 81], ["2026-07-19", 82]],
            ),
            (
                "Second",
                DAILY_HEADERS,
                [["2026-07-20", 83], ["2026-07-21", 84]],
            ),
        ]
    )

    with pytest.raises(ParseFailure, match="10,000 rows"):
        parse_source_file("too-many.xlsx", payload)

