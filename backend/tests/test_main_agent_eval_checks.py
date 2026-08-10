from __future__ import annotations

from typing import Any

from evals.deterministic import (
    ANSWER_FORBIDDEN,
    ANSWER_RECOMMENDATIONS,
    ANSWER_REQUIRED,
    EVIDENCE_FACT_VALUES,
    EVIDENCE_METRICS,
    LATENCY_BUDGET,
    ROUTE_MODE,
    ROUTE_SKILL,
    TERMINAL_CONSISTENCY,
    TOOLS_FORBIDDEN,
    TOOLS_REQUIRED,
    check_answer_boundaries,
    check_evidence,
    check_expert_budget,
    check_latency,
    check_retry_budget,
    check_route,
    check_scope,
    check_terminals,
    check_tools,
    run_deterministic_checks,
)
from evals.models import EvaluationCase, EvaluationExpectation, EvaluationObservation


def _case(**expectation_overrides: object) -> EvaluationCase:
    expectation: dict[str, object] = {
        "expected_mode": "skill",
        "expected_skill_code": "account_data_analysis",
        "required_tools": ["account.metrics_analysis"],
        "forbidden_tools": ["strategy.generate"],
        "expected_answerability": "full",
        "required_claims": ["performance_summary"],
        "forbidden_claims": ["unsupported_numeric_claim"],
        "required_evidence_metrics": ["play"],
        "maximum_expert_invocations": 2,
        "maximum_retry_count": 1,
        "allowed_terminal_statuses": ["completed"],
        "latency_budget_ms": 2_000,
    }
    expectation.update(expectation_overrides)
    return EvaluationCase(
        case_id="analysis-summary-01",
        version="1.0.0",
        category="metric_analysis",
        description="analyze selected account performance",
        account_fixture="complete_30d",
        messages=("最近30天账号表现怎么样？",),
        expectation=EvaluationExpectation.model_validate(expectation),
    )


def _observation(**overrides: Any) -> EvaluationObservation:
    payload: dict[str, Any] = {
        "case_id": "analysis-summary-01",
        "org_id": 1,
        "user_id": 2,
        "account_id": 3,
        "thread_id": 4,
        "turn_id": 5,
        "route_mode": "skill",
        "route_skill_code": "account_data_analysis",
        "tool_calls": ({"tool_code": "account.metrics_analysis", "retry_count": 0},),
        "skill_runs": (),
        "expert_invocations": (),
        "evidence_refs": (
            {"account_id": 3, "metric_code": "play", "value": 700, "unit": "count"},
        ),
        "answer_payload": {
            "answerability": "full",
            "claims": ["performance_summary"],
            "key_facts": [
                {"metric_code": "play", "current_value": 700, "unit": "count"}
            ],
            "recommendations": [],
        },
        "final_answer": "最近30天播放量为700。",
        "terminal_states": {"turn": "completed", "run": "completed", "skill": "completed"},
        "timings_ms": {"total": 900},
    }
    payload.update(overrides)
    return EvaluationObservation.model_validate(payload)


def _by_code(results: tuple[Any, ...], code: str) -> Any:
    return next(result for result in results if result.code == code)


def test_scope_check_rejects_foreign_evidence_account() -> None:
    observation = _observation(
        evidence_refs=({"account_id": 4, "metric_code": "play", "value": 700},),
    )

    result = check_scope(_case(), observation)

    assert result.passed is False
    assert result.severity == "p0"
    assert result.details["foreign_accounts"] == [4]


def test_route_checks_report_mode_and_skill_independently() -> None:
    results = check_route(
        _case(),
        _observation(route_mode="query", route_skill_code="account_data_query"),
    )

    assert _by_code(results, ROUTE_MODE).passed is False
    assert _by_code(results, ROUTE_SKILL).passed is False


def test_tool_checks_reject_missing_required_and_invoked_forbidden_tools() -> None:
    results = check_tools(
        _case(),
        _observation(tool_calls=({"tool_code": "strategy.generate", "retry_count": 0},)),
    )

    assert _by_code(results, TOOLS_REQUIRED).details["missing"] == [
        "account.metrics_analysis"
    ]
    assert _by_code(results, TOOLS_FORBIDDEN).details["invoked"] == ["strategy.generate"]


def test_expert_check_enforces_maximum_invocations() -> None:
    observation = _observation(
        expert_invocations=({"agent_code": "a"}, {"agent_code": "b"}, {"agent_code": "c"})
    )

    result = check_expert_budget(_case(), observation)

    assert result.passed is False
    assert result.details == {"actual": 3, "maximum": 2}


def test_retry_check_sums_normalized_tool_retry_counts() -> None:
    observation = _observation(
        tool_calls=(
            {"tool_code": "account.metrics_analysis", "retry_count": 1},
            {"tool_code": "account.data_context", "retry_count": 1},
        )
    )

    result = check_retry_budget(_case(), observation)

    assert result.passed is False
    assert result.details == {"actual": 2, "maximum": 1}


def test_evidence_check_rejects_numeric_fact_without_matching_value_and_unit() -> None:
    observation = _observation(
        answer_payload={
            "answerability": "full",
            "claims": ["performance_summary"],
            "key_facts": [
                {"metric_code": "play", "current_value": 701, "unit": "count"}
            ],
        },
    )

    results = check_evidence(_case(), observation)

    assert _by_code(results, EVIDENCE_FACT_VALUES).passed is False


def test_evidence_check_normalizes_only_explicit_percentage_unit_aliases() -> None:
    observation = _observation(
        evidence_refs=(
            {"account_id": 3, "metric_code": "completion_rate", "value": 15.6, "unit": "%"},
        ),
        answer_payload={
            "answerability": "full",
            "claims": ["performance_summary"],
            "key_facts": [
                {
                    "metric_code": "completion_rate",
                    "current_value": 15.6,
                    "unit": "percent",
                }
            ],
        },
    )
    case = _case(required_evidence_metrics=["completion_rate"])

    results = check_evidence(case, observation)

    assert _by_code(results, EVIDENCE_FACT_VALUES).passed is True
    assert _by_code(results, EVIDENCE_METRICS).passed is True


def test_evidence_check_rejects_missing_required_metric() -> None:
    results = check_evidence(
        _case(required_evidence_metrics=["share"]),
        _observation(),
    )

    assert _by_code(results, EVIDENCE_METRICS).details["missing"] == ["share"]


def test_boundary_checks_require_structured_claims_and_expected_answerability() -> None:
    observation = _observation(
        answer_payload={"answerability": "partial", "claims": [], "key_facts": []}
    )

    results = check_answer_boundaries(_case(), observation)

    required = _by_code(results, ANSWER_REQUIRED)
    assert required.passed is False
    assert required.details["missing"] == ["performance_summary"]
    assert required.details["answerability_matches"] is False


def test_boundary_check_rejects_strategy_when_user_forbids_it() -> None:
    case = _case(forbidden_claims=["30天策略"])
    observation = _observation(final_answer="下面是30天策略")

    results = check_answer_boundaries(case, observation)

    assert _by_code(results, ANSWER_FORBIDDEN).passed is False


def test_boundary_check_accepts_pending_only_insufficient_answer() -> None:
    case = _case(
        expected_answerability="insufficient",
        required_claims=["pending_data_excluded"],
        forbidden_claims=["pending_data_confirmed"],
        required_evidence_metrics=[],
    )
    observation = _observation(
        evidence_refs=(),
        answer_payload={
            "answerability": "insufficient",
            "claims": ["pending_data_excluded"],
            "key_facts": [],
        },
        final_answer="待确认数据不会进入正式分析，请先确认写入。",
    )

    results = check_answer_boundaries(case, observation)

    assert _by_code(results, ANSWER_REQUIRED).passed is True
    assert _by_code(results, ANSWER_FORBIDDEN).passed is True


def test_boundary_check_requires_explicit_missing_comparison_claim() -> None:
    case = _case(
        expected_answerability="partial",
        required_claims=["missing_comparison_period"],
    )

    results = check_answer_boundaries(case, _observation())

    assert _by_code(results, ANSWER_REQUIRED).passed is False


def test_recommendation_check_enforces_maximum_three_when_requested() -> None:
    case = _case(required_claims=["recommendations_max_3"])
    recommendations = [
        {"action": f"action-{index}", "metric": "play", "observation_days": 7}
        for index in range(4)
    ]
    observation = _observation(
        answer_payload={
            "answerability": "full",
            "claims": ["recommendations_max_3"],
            "key_facts": [],
            "recommendations": recommendations,
        }
    )

    results = check_answer_boundaries(case, observation)

    assert _by_code(results, ANSWER_RECOMMENDATIONS).passed is False


def test_recommendation_check_requires_action_metric_and_positive_days() -> None:
    case = _case(required_claims=["action_with_metric_and_days"])
    observation = _observation(
        answer_payload={
            "answerability": "full",
            "claims": ["action_with_metric_and_days"],
            "key_facts": [],
            "recommendations": [{"action": "测试新开头", "metric": "", "observation_days": 0}],
        }
    )

    results = check_answer_boundaries(case, observation)

    assert _by_code(results, ANSWER_RECOMMENDATIONS).passed is False


def test_safe_degradation_requires_nonblank_final_answer() -> None:
    case = _case(expected_answerability="partial")

    results = check_answer_boundaries(case, _observation(final_answer="   "))

    assert _by_code(results, ANSWER_REQUIRED).passed is False
    assert _by_code(results, ANSWER_REQUIRED).details["nonblank"] is False


def test_terminal_check_rejects_dead_letter_run_with_running_turn() -> None:
    observation = _observation(
        terminal_states={"turn": "running", "run": "dead_letter", "skill": "failed"}
    )

    result = check_terminals(_case(), observation)

    assert result.code == TERMINAL_CONSISTENCY
    assert result.passed is False


def test_latency_check_is_report_only_when_budget_is_exceeded() -> None:
    result = check_latency(_case(), _observation(timings_ms={"total": 2_001}))

    assert result.code == LATENCY_BUDGET
    assert result.severity == "info"
    assert result.passed is False


def test_composed_checks_have_stable_unique_order() -> None:
    results = run_deterministic_checks(_case(), _observation())
    codes = [result.code for result in results]

    assert codes == [
        "scope.account",
        "route.mode",
        "route.skill",
        "tools.required",
        "tools.forbidden",
        "experts.maximum",
        "retries.maximum",
        "evidence.account",
        "evidence.metrics",
        "evidence.fact_values",
        "answer.required_claims",
        "answer.forbidden_claims",
        "answer.recommendations",
        "terminal.consistency",
        "latency.budget",
    ]
    assert len(codes) == len(set(codes))
