import json
from pathlib import Path

import pytest

from evals.case_loader import load_evaluation_cases


def _payload(case_id: str = "data-exists-01", version: str = "1.0.0") -> dict[str, object]:
    return {
        "case_id": case_id,
        "version": version,
        "category": "data_query",
        "description": "query the selected account data",
        "account_fixture": "complete_30d",
        "messages": ["我现在账号有数据吗？"],
        "expectation": {
            "expected_mode": "query",
            "expected_skill_code": "account_data_query",
        },
    }


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_loader_reads_utf8_cases_in_stable_file_order(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    _write(path, [_payload("data-first-01"), _payload("data-second-02")])

    cases = load_evaluation_cases(path)

    assert [case.case_id for case in cases] == ["data-first-01", "data-second-02"]
    assert cases[0].messages == ("我现在账号有数据吗？",)


def test_loader_rejects_non_array_payload(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    _write(path, {"cases": [_payload()]})

    with pytest.raises(ValueError, match="JSON array"):
        load_evaluation_cases(path)


def test_loader_rejects_duplicate_case_id_and_version(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    _write(path, [_payload(), _payload()])

    with pytest.raises(ValueError, match=r"case_id \+ version"):
        load_evaluation_cases(path)


def test_loader_allows_same_case_id_at_distinct_versions(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    _write(path, [_payload(version="1.0.0"), _payload(version="1.1.0")])

    cases = load_evaluation_cases(path)

    assert [case.version for case in cases] == ["1.0.0", "1.1.0"]
