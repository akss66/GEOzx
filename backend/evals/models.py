from __future__ import annotations

from datetime import datetime
from math import fsum
from statistics import mean
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EvaluationMode = Literal["deterministic", "live-model"]
CheckSeverity = Literal["p0", "p1", "info"]


def _normalized_unique_strings(values: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{field_name} must be a list or tuple")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must contain strings")
        item = value.strip()
        if not item:
            raise ValueError(f"{field_name} must not contain empty values")
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must contain unique values")
    return tuple(normalized)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationExpectation(FrozenModel):
    expected_mode: str | None = None
    expected_skill_code: str | None = None
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    expected_answerability: str | None = None
    required_claims: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    required_evidence_metrics: tuple[str, ...] = ()
    maximum_expert_invocations: int | None = Field(default=None, ge=0, le=10)
    maximum_retry_count: int | None = Field(default=None, ge=0, le=10)
    allowed_terminal_statuses: tuple[str, ...] = ("completed",)
    latency_budget_ms: int | None = Field(default=None, ge=1, le=300_000)

    @field_validator(
        "required_tools",
        "forbidden_tools",
        "required_claims",
        "forbidden_claims",
        "required_evidence_metrics",
        "allowed_terminal_statuses",
        mode="before",
    )
    @classmethod
    def normalize_unique_contract_items(cls, values: object, info: Any) -> tuple[str, ...]:
        return _normalized_unique_strings(values, field_name=info.field_name)


class EvaluationCase(FrozenModel):
    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,79}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    category: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=5, max_length=500)
    account_fixture: str = Field(min_length=2, max_length=80)
    messages: tuple[str, ...] = Field(min_length=1, max_length=5)
    requested_skill_code: str | None = Field(default=None, max_length=120)
    expectation: EvaluationExpectation

    @field_validator("messages", mode="before")
    @classmethod
    def normalize_messages(cls, values: object) -> tuple[str, ...]:
        return _normalized_unique_strings(values, field_name="messages")


class EvaluationObservation(FrozenModel):
    case_id: str = Field(min_length=1, max_length=80)
    org_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    account_id: int = Field(gt=0)
    thread_id: int = Field(gt=0)
    turn_id: int = Field(gt=0)
    route_mode: str | None = None
    route_skill_code: str | None = None
    tool_calls: tuple[dict[str, Any], ...] = ()
    skill_runs: tuple[dict[str, Any], ...] = ()
    expert_invocations: tuple[dict[str, Any], ...] = ()
    evidence_refs: tuple[dict[str, Any], ...] = ()
    answer_payload: dict[str, Any] = Field(default_factory=dict)
    final_answer: str = ""
    terminal_states: dict[str, str] = Field(default_factory=dict)
    timings_ms: dict[str, int | None] = Field(default_factory=dict)
    model_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timings_ms")
    @classmethod
    def reject_negative_timings(cls, values: dict[str, int | None]) -> dict[str, int | None]:
        if any(value is not None and value < 0 for value in values.values()):
            raise ValueError("timings_ms values must be non-negative")
        return values


class CheckResult(FrozenModel):
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,119}$")
    severity: CheckSeverity
    passed: bool
    message: str = Field(min_length=1, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)


class SemanticScore(FrozenModel):
    metric: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,119}$")
    score: float = Field(ge=0, le=1)
    threshold: float = Field(default=0.8, ge=0, le=1)
    passed: bool
    reason: str = Field(min_length=1, max_length=1_000)
    cost_cny: float | None = Field(default=None, ge=0)

    @classmethod
    def from_score(
        cls,
        *,
        metric: str,
        score: float,
        threshold: float = 0.8,
        reason: str,
        cost_cny: float | None = None,
    ) -> Self:
        return cls(
            metric=metric,
            score=score,
            threshold=threshold,
            passed=score >= threshold,
            reason=reason,
            cost_cny=cost_cny,
        )

    @model_validator(mode="after")
    def validate_derived_passed(self) -> Self:
        if self.passed != (self.score >= self.threshold):
            raise ValueError("passed must match score >= threshold")
        return self


class EvaluationRecord(FrozenModel):
    case_id: str
    case_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    mode: EvaluationMode
    started_at: datetime
    duration_ms: int = Field(ge=0)
    observation: EvaluationObservation
    deterministic_checks: tuple[CheckResult, ...]
    semantic_scores: tuple[SemanticScore, ...] = ()
    passed: bool
    failure_reasons: tuple[str, ...] = ()

    @classmethod
    def from_results(
        cls,
        *,
        case_id: str,
        case_version: str,
        mode: EvaluationMode,
        started_at: datetime,
        duration_ms: int,
        observation: EvaluationObservation,
        deterministic_checks: tuple[CheckResult, ...],
        semantic_scores: tuple[SemanticScore, ...] = (),
    ) -> Self:
        failure_reasons = cls._failure_reasons(deterministic_checks, semantic_scores)
        return cls(
            case_id=case_id,
            case_version=case_version,
            mode=mode,
            started_at=started_at,
            duration_ms=duration_ms,
            observation=observation,
            deterministic_checks=deterministic_checks,
            semantic_scores=semantic_scores,
            passed=not failure_reasons,
            failure_reasons=failure_reasons,
        )

    @staticmethod
    def _failure_reasons(
        checks: tuple[CheckResult, ...],
        scores: tuple[SemanticScore, ...],
    ) -> tuple[str, ...]:
        deterministic = tuple(
            result.code for result in checks if result.severity == "p0" and not result.passed
        )
        semantic = tuple(f"semantic.{score.metric}" for score in scores if not score.passed)
        return deterministic + semantic

    @model_validator(mode="after")
    def validate_derived_status(self) -> Self:
        expected_reasons = self._failure_reasons(
            self.deterministic_checks,
            self.semantic_scores,
        )
        if self.failure_reasons != expected_reasons or self.passed != (not expected_reasons):
            raise ValueError("record status must be derived from blocking results")
        return self


class EvaluationBatchReport(FrozenModel):
    suite_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,119}$")
    suite_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    mode: EvaluationMode
    git_commit: str = Field(min_length=4, max_length=64)
    records: tuple[EvaluationRecord, ...]
    passed: bool
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    semantic_average: float | None = Field(default=None, ge=0, le=1)
    semantic_cost_cny: float = Field(default=0, ge=0)

    @classmethod
    def from_records(
        cls,
        *,
        suite_id: str,
        suite_version: str,
        mode: EvaluationMode,
        git_commit: str,
        records: tuple[EvaluationRecord, ...],
        semantic_cost_cny: float | None = None,
    ) -> Self:
        semantic_values = [score.score for record in records for score in record.semantic_scores]
        semantic_average = mean(semantic_values) if semantic_values else None
        score_cost_cny = fsum(
            score.cost_cny or 0 for record in records for score in record.semantic_scores
        )
        reported_cost_cny = score_cost_cny if semantic_cost_cny is None else semantic_cost_cny
        if reported_cost_cny < score_cost_cny:
            raise ValueError("reported semantic cost cannot be less than scored cost")
        passed_count = sum(record.passed for record in records)
        failed_count = len(records) - passed_count
        passed = failed_count == 0 and (semantic_average is None or semantic_average >= 0.85)
        return cls(
            suite_id=suite_id,
            suite_version=suite_version,
            mode=mode,
            git_commit=git_commit,
            records=records,
            passed=passed,
            passed_count=passed_count,
            failed_count=failed_count,
            semantic_average=semantic_average,
            semantic_cost_cny=reported_cost_cny,
        )

    @model_validator(mode="after")
    def validate_derived_status(self) -> Self:
        expected_passed_count = sum(record.passed for record in self.records)
        expected_failed_count = len(self.records) - expected_passed_count
        semantic_values = [
            score.score for record in self.records for score in record.semantic_scores
        ]
        expected_average = mean(semantic_values) if semantic_values else None
        minimum_cost_cny = fsum(
            score.cost_cny or 0 for record in self.records for score in record.semantic_scores
        )
        expected_passed = expected_failed_count == 0 and (
            expected_average is None or expected_average >= 0.85
        )
        if (
            self.passed_count != expected_passed_count
            or self.failed_count != expected_failed_count
            or self.semantic_average != expected_average
            or self.semantic_cost_cny < minimum_cost_cny
            or self.passed != expected_passed
        ):
            raise ValueError("batch status must be derived from records")
        return self
