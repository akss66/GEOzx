from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from evals.models import CheckResult, EvaluationCase, EvaluationObservation

SCOPE_ACCOUNT = "scope.account"
ROUTE_MODE = "route.mode"
ROUTE_SKILL = "route.skill"
TOOLS_REQUIRED = "tools.required"
TOOLS_FORBIDDEN = "tools.forbidden"
EXPERT_BUDGET = "experts.maximum"
RETRY_BUDGET = "retries.maximum"
EVIDENCE_ACCOUNT = "evidence.account"
EVIDENCE_METRICS = "evidence.metrics"
EVIDENCE_FACT_VALUES = "evidence.fact_values"
ANSWER_REQUIRED = "answer.required_claims"
ANSWER_FORBIDDEN = "answer.forbidden_claims"
ANSWER_RECOMMENDATIONS = "answer.recommendations"
TERMINAL_CONSISTENCY = "terminal.consistency"
LATENCY_BUDGET = "latency.budget"

_ACTIVE_STATUSES = frozenset({"pending", "queued", "running", "retrying"})
_UNIT_ALIASES = {
    "%": "percent",
    "pct": "percent",
    "percent": "percent",
    "percentage": "percent",
    "count": "count",
    "counts": "count",
    "次": "count",
    "个": "count",
    "rate": "ratio",
    "ratio": "ratio",
}


def _result(
    code: str,
    severity: str,
    passed: bool,
    **details: Any,
) -> CheckResult:
    state = "passed" if passed else "failed"
    return CheckResult(
        code=code,
        severity=severity,
        passed=passed,
        message=f"{code} {state}",
        details=details,
    )


def _normalized_account_ids(items: tuple[dict[str, Any], ...]) -> list[int | str]:
    account_ids: set[int | str] = set()
    for item in items:
        value = item.get("account_id")
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, int):
            account_ids.add(value)
        else:
            account_ids.add(str(value))
    return sorted(account_ids, key=str)


def check_scope(
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> CheckResult:
    scoped_items = (
        observation.tool_calls
        + observation.skill_runs
        + observation.expert_invocations
        + observation.evidence_refs
    )
    observed_accounts = _normalized_account_ids(scoped_items)
    foreign_accounts = [
        account_id
        for account_id in observed_accounts
        if str(account_id) != str(observation.account_id)
    ]
    case_matches = observation.case_id == case.case_id
    return _result(
        SCOPE_ACCOUNT,
        "p0",
        case_matches and not foreign_accounts,
        case_matches=case_matches,
        foreign_accounts=foreign_accounts,
    )


def check_route(
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> tuple[CheckResult, CheckResult]:
    expected = case.expectation
    mode_matches = (
        expected.expected_mode is None or observation.route_mode == expected.expected_mode
    )
    skill_matches = (
        expected.expected_skill_code is None
        or observation.route_skill_code == expected.expected_skill_code
    )
    return (
        _result(
            ROUTE_MODE,
            "p0",
            mode_matches,
            expected=expected.expected_mode,
            actual=observation.route_mode,
        ),
        _result(
            ROUTE_SKILL,
            "p0",
            skill_matches,
            expected=expected.expected_skill_code,
            actual=observation.route_skill_code,
        ),
    )


def check_tools(
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> tuple[CheckResult, CheckResult]:
    invoked = {
        str(item.get("tool_code") or "")
        for item in observation.tool_calls
        if item.get("tool_code")
    }
    missing = sorted(set(case.expectation.required_tools) - invoked)
    forbidden = sorted(set(case.expectation.forbidden_tools) & invoked)
    return (
        _result(TOOLS_REQUIRED, "p0", not missing, missing=missing),
        _result(TOOLS_FORBIDDEN, "p0", not forbidden, invoked=forbidden),
    )


def check_expert_budget(
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> CheckResult:
    actual = len(observation.expert_invocations)
    maximum = case.expectation.maximum_expert_invocations
    passed = maximum is None or actual <= maximum
    return _result(EXPERT_BUDGET, "p0", passed, actual=actual, maximum=maximum)


def check_retry_budget(
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> CheckResult:
    actual = 0
    for call in observation.tool_calls:
        value = call.get("retry_count", 0)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            actual += value
    maximum = case.expectation.maximum_retry_count
    passed = maximum is None or actual <= maximum
    return _result(RETRY_BUDGET, "p0", passed, actual=actual, maximum=maximum)


def _canonical_unit(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return _UNIT_ALIASES.get(normalized, normalized)


def _numeric_key(
    item: Mapping[str, Any],
    *,
    value_field: str,
) -> tuple[str, Decimal, str] | None:
    value = item.get(value_field)
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return (
        str(item.get("metric_code") or ""),
        number,
        _canonical_unit(item.get("unit")),
    )


def check_evidence(
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> tuple[CheckResult, CheckResult, CheckResult]:
    foreign_accounts = [
        account_id
        for account_id in _normalized_account_ids(observation.evidence_refs)
        if str(account_id) != str(observation.account_id)
    ]
    observed_metrics = {
        str(item.get("metric_code") or "") for item in observation.evidence_refs
    }
    missing_metrics = sorted(
        set(case.expectation.required_evidence_metrics) - observed_metrics
    )
    allowed = {
        key
        for item in observation.evidence_refs
        if (key := _numeric_key(item, value_field="value")) is not None
    }
    raw_facts = observation.answer_payload.get("key_facts", [])
    facts = raw_facts if isinstance(raw_facts, list) else []
    reported = {
        key
        for fact in facts
        if isinstance(fact, Mapping)
        and (key := _numeric_key(fact, value_field="current_value")) is not None
    }
    missing_values = sorted(reported - allowed)
    return (
        _result(
            EVIDENCE_ACCOUNT,
            "p0",
            not foreign_accounts,
            foreign_accounts=foreign_accounts,
        ),
        _result(EVIDENCE_METRICS, "p0", not missing_metrics, missing=missing_metrics),
        _result(
            EVIDENCE_FACT_VALUES,
            "p0",
            not missing_values,
            missing=[tuple(map(str, item)) for item in missing_values],
        ),
    )


def _structured_claims(observation: EvaluationObservation) -> set[str]:
    raw_claims = observation.answer_payload.get("claims", [])
    if not isinstance(raw_claims, list):
        return set()
    return {str(item).strip() for item in raw_claims if str(item).strip()}


def _forbidden_text_hits(forbidden: set[str], answer: str) -> list[str]:
    return sorted(
        claim
        for claim in forbidden
        if any(ord(character) > 127 for character in claim) and claim in answer
    )


def _recommendations_are_complete(recommendations: list[object]) -> bool:
    if not recommendations:
        return False
    for item in recommendations:
        if not isinstance(item, Mapping):
            return False
        action = item.get("action")
        metric = item.get("metric")
        days = item.get("observation_days")
        if not isinstance(action, str) or not action.strip():
            return False
        if not isinstance(metric, str) or not metric.strip():
            return False
        if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
            return False
    return True


def check_answer_boundaries(
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> tuple[CheckResult, CheckResult, CheckResult]:
    expectation = case.expectation
    claims = _structured_claims(observation)
    missing = sorted(set(expectation.required_claims) - claims)
    answerability = observation.answer_payload.get("answerability")
    answerability_matches = (
        expectation.expected_answerability is None
        or answerability == expectation.expected_answerability
    )
    nonblank = bool(observation.final_answer.strip())

    forbidden = set(expectation.forbidden_claims)
    forbidden_hits = sorted(forbidden & claims)
    forbidden_text_hits = _forbidden_text_hits(forbidden, observation.final_answer)

    raw_recommendations = observation.answer_payload.get("recommendations", [])
    recommendations = raw_recommendations if isinstance(raw_recommendations, list) else []
    requires_maximum_three = "recommendations_max_3" in expectation.required_claims
    requires_complete_action = "action_with_metric_and_days" in expectation.required_claims
    recommendation_passed = True
    if requires_maximum_three:
        recommendation_passed = 1 <= len(recommendations) <= 3
    if requires_complete_action:
        recommendation_passed = recommendation_passed and _recommendations_are_complete(
            recommendations
        )

    return (
        _result(
            ANSWER_REQUIRED,
            "p0",
            not missing and answerability_matches and nonblank,
            missing=missing,
            answerability_matches=answerability_matches,
            expected_answerability=expectation.expected_answerability,
            actual_answerability=answerability,
            nonblank=nonblank,
        ),
        _result(
            ANSWER_FORBIDDEN,
            "p0",
            not forbidden_hits and not forbidden_text_hits,
            claims=forbidden_hits,
            text=forbidden_text_hits,
        ),
        _result(
            ANSWER_RECOMMENDATIONS,
            "p0",
            recommendation_passed,
            count=len(recommendations),
            maximum_three=requires_maximum_three,
            complete_action=requires_complete_action,
        ),
    )


def check_terminals(
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> CheckResult:
    states = dict(observation.terminal_states)
    allowed = set(case.expectation.allowed_terminal_statuses)
    active = sorted(name for name, status in states.items() if status in _ACTIVE_STATUSES)
    disallowed = {
        name: status for name, status in states.items() if status not in allowed
    }
    passed = bool(states) and not active and not disallowed
    return _result(
        TERMINAL_CONSISTENCY,
        "p0",
        passed,
        active=active,
        disallowed=disallowed,
        allowed=sorted(allowed),
    )


def check_latency(
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> CheckResult:
    budget = case.expectation.latency_budget_ms
    actual = observation.timings_ms.get("total")
    if actual is None:
        actual = observation.timings_ms.get("total_ms")
    passed = budget is None or (actual is not None and actual <= budget)
    return _result(LATENCY_BUDGET, "info", passed, actual=actual, budget=budget)


def run_deterministic_checks(
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> tuple[CheckResult, ...]:
    return (
        check_scope(case, observation),
        *check_route(case, observation),
        *check_tools(case, observation),
        check_expert_budget(case, observation),
        check_retry_budget(case, observation),
        *check_evidence(case, observation),
        *check_answer_boundaries(case, observation),
        check_terminals(case, observation),
        check_latency(case, observation),
    )
