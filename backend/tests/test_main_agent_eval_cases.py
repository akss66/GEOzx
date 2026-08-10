import json
from collections import Counter
from pathlib import Path

import pytest

from evals.case_loader import load_evaluation_cases

CASES = Path(__file__).parents[1] / "evals/cases/account_analysis_v1.json"


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


def test_account_analysis_v1_contains_exactly_thirty_versioned_cases() -> None:
    cases = load_evaluation_cases(CASES)

    assert len(cases) == 30
    assert {case.version for case in cases} == {"1.0.0"}
    assert Counter(case.category for case in cases) == {
        "data_query": 5,
        "metric_analysis": 5,
        "data_limits": 5,
        "diagnosis_and_advice": 5,
        "instruction_boundaries": 5,
        "failure_and_isolation": 5,
    }


def test_account_analysis_v1_covers_all_p0_safety_properties() -> None:
    cases = load_evaluation_cases(CASES)
    serialized = json.dumps([case.model_dump() for case in cases], ensure_ascii=False)

    for required in (
        "projectless_complete_30d",
        "business_conflict",
        "expert_failure_after_tool",
        "critic_unavailable",
        "two_accounts",
        "不要生成30天策略",
    ):
        assert required in serialized


def test_every_v1_case_declares_routing_and_safety_expectations() -> None:
    cases = load_evaluation_cases(CASES)

    for case in cases:
        expectation = case.expectation
        assert expectation.expected_mode is not None, case.case_id
        assert expectation.expected_skill_code is not None, case.case_id
        assert expectation.expected_answerability is not None, case.case_id
        assert expectation.maximum_expert_invocations is not None, case.case_id
        assert expectation.maximum_retry_count is not None, case.case_id
        assert expectation.allowed_terminal_statuses, case.case_id
