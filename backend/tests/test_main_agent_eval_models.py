from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from evals.models import (
    CheckResult,
    EvaluationBatchReport,
    EvaluationCase,
    EvaluationExpectation,
    EvaluationObservation,
    EvaluationRecord,
    SemanticScore,
)


def _expectation(**overrides: object) -> EvaluationExpectation:
    payload: dict[str, object] = {
        "expected_mode": "query",
        "expected_skill_code": "account_data_query",
    }
    payload.update(overrides)
    return EvaluationExpectation.model_validate(payload)


def _case(**overrides: object) -> EvaluationCase:
    payload: dict[str, object] = {
        "case_id": "data-exists-01",
        "version": "1.0.0",
        "category": "data_query",
        "description": "query the selected account data",
        "account_fixture": "complete_30d",
        "messages": ["我现在账号有数据吗？"],
        "expectation": _expectation(),
    }
    payload.update(overrides)
    return EvaluationCase.model_validate(payload)


def _observation(case_id: str = "data-exists-01") -> EvaluationObservation:
    return EvaluationObservation(
        case_id=case_id,
        org_id=1,
        user_id=2,
        account_id=3,
        thread_id=4,
        turn_id=5,
        route_mode="query",
        route_skill_code="account_data_query",
        final_answer="当前账号已有已确认数据。",
    )


def _passing_record() -> EvaluationRecord:
    return EvaluationRecord.from_results(
        case_id="data-exists-01",
        case_version="1.0.0",
        mode="deterministic",
        started_at=datetime(2026, 8, 10, tzinfo=UTC),
        duration_ms=12,
        observation=_observation(),
        deterministic_checks=(
            CheckResult(
                code="scope.account",
                severity="p0",
                passed=True,
                message="account scope matches",
            ),
        ),
    )


def test_case_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EvaluationCase.model_validate(
            {
                "case_id": "data-exists-01",
                "version": "1.0.0",
                "category": "data_query",
                "description": "query current account data",
                "account_fixture": "complete_30d",
                "messages": ["我现在账号有数据吗？"],
                "expectation": {},
                "unexpected": True,
            }
        )


@pytest.mark.parametrize("case_id", ["UPPERCASE", "ab", "contains_space"])
def test_case_rejects_unstable_case_ids(case_id: str) -> None:
    with pytest.raises(ValidationError):
        _case(case_id=case_id)


@pytest.mark.parametrize(
    "messages",
    [
        [""],
        ["   "],
        ["重复", "重复"],
        [" 重复 ", "重复"],
    ],
)
def test_case_requires_unique_nonempty_messages(messages: list[str]) -> None:
    with pytest.raises(ValidationError):
        _case(messages=messages)


def test_case_trims_messages_before_persisting() -> None:
    case = _case(messages=["  当前账号有数据吗？  "])

    assert case.messages == ("当前账号有数据吗？",)


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("required_tools", ["account.data", "account.data"]),
        ("forbidden_tools", ["account.write", "account.write"]),
        ("required_claims", ["data_exists", "data_exists"]),
        ("forbidden_claims", ["trend_down", "trend_down"]),
        ("required_evidence_metrics", ["play", "play"]),
    ],
)
def test_expectation_rejects_duplicate_contract_items(
    field: str,
    values: list[str],
) -> None:
    with pytest.raises(ValidationError):
        _expectation(**{field: values})


def test_expectation_enforces_bounded_budgets() -> None:
    with pytest.raises(ValidationError):
        _expectation(maximum_expert_invocations=11)
    with pytest.raises(ValidationError):
        _expectation(maximum_retry_count=-1)
    with pytest.raises(ValidationError):
        _expectation(latency_budget_ms=300_001)


def test_semantic_score_derives_passed_from_score_and_threshold() -> None:
    passing = SemanticScore.from_score(
        metric="faithfulness",
        score=0.8,
        threshold=0.8,
        reason="grounded in imported data",
    )
    failing = SemanticScore.from_score(
        metric="faithfulness",
        score=0.79,
        threshold=0.8,
        reason="one claim lacks evidence",
    )

    assert passing.passed is True
    assert failing.passed is False


def test_record_factory_derives_failure_reasons_from_blocking_results() -> None:
    record = EvaluationRecord.from_results(
        case_id="data-exists-01",
        case_version="1.0.0",
        mode="live-model",
        started_at=datetime(2026, 8, 10, tzinfo=UTC),
        duration_ms=20,
        observation=_observation(),
        deterministic_checks=(
            CheckResult(code="scope.account", severity="p0", passed=False, message="mismatch"),
            CheckResult(code="latency.total", severity="info", passed=False, message="slow"),
        ),
        semantic_scores=(
            SemanticScore.from_score(
                metric="faithfulness",
                score=0.7,
                threshold=0.8,
                reason="unsupported claim",
            ),
        ),
    )

    assert record.passed is False
    assert record.failure_reasons == ("scope.account", "semantic.faithfulness")


def test_batch_report_round_trips_as_json() -> None:
    report = EvaluationBatchReport.from_records(
        suite_id="account-analysis-v1",
        suite_version="1.0.0",
        mode="deterministic",
        git_commit="abc1234",
        records=(_passing_record(),),
    )

    assert report.passed is True
    assert report.passed_count == 1
    assert report.failed_count == 0
    assert EvaluationBatchReport.model_validate_json(report.model_dump_json()) == report


def test_all_persisted_contracts_are_frozen() -> None:
    case = _case()

    with pytest.raises(ValidationError):
        case.case_id = "changed-id"  # type: ignore[misc]
