from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol

from evals.deterministic import run_deterministic_checks
from evals.models import (
    CheckResult,
    EvaluationBatchReport,
    EvaluationCase,
    EvaluationMode,
    EvaluationObservation,
    EvaluationRecord,
    SemanticScore,
)


class CaseExecutor(Protocol):
    async def execute(self, case: EvaluationCase) -> EvaluationObservation: ...


class SemanticEvaluator(Protocol):
    async def evaluate(
        self,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> tuple[SemanticScore, ...]: ...


class EvaluationRunner:
    def __init__(
        self,
        executor: CaseExecutor,
        semantic: SemanticEvaluator | None = None,
    ) -> None:
        self._executor = executor
        self._semantic = semantic

    async def run(
        self,
        cases: Sequence[EvaluationCase],
        *,
        mode: EvaluationMode,
        git_commit: str,
    ) -> EvaluationBatchReport:
        case_tuple = tuple(cases)
        if not case_tuple:
            raise ValueError("evaluation runner requires at least one case")
        versions = {case.version for case in case_tuple}
        if len(versions) != 1:
            raise ValueError("one evaluation batch must use exactly one case version")
        if mode == "live-model" and self._semantic is None:
            raise ValueError("live-model mode requires a semantic evaluator")

        records: list[EvaluationRecord] = []
        for case in case_tuple:
            records.append(await self._run_case(case, mode=mode))
        return EvaluationBatchReport.from_records(
            suite_id="account-analysis-v1",
            suite_version=next(iter(versions)),
            mode=mode,
            git_commit=git_commit,
            records=tuple(records),
            semantic_cost_cny=_semantic_cost_cny(self._semantic),
        )

    async def _run_case(
        self,
        case: EvaluationCase,
        *,
        mode: EvaluationMode,
    ) -> EvaluationRecord:
        started_at = datetime.now(UTC)
        started = perf_counter()
        try:
            observation = await self._executor.execute(case)
        except Exception:  # noqa: BLE001 - evaluation exceptions become bounded records
            return EvaluationRecord.from_results(
                case_id=case.case_id,
                case_version=case.version,
                mode=mode,
                started_at=started_at,
                duration_ms=max(0, int((perf_counter() - started) * 1_000)),
                observation=_failed_observation(case.case_id),
                deterministic_checks=(
                    CheckResult(
                        code="runner.exception",
                        severity="p0",
                        passed=False,
                        message="case executor failed",
                    ),
                ),
            )

        checks = list(run_deterministic_checks(case, observation))
        semantic_scores: tuple[SemanticScore, ...] = ()
        if mode == "live-model" and self._semantic is not None:
            try:
                semantic_scores = await self._semantic.evaluate(case, observation)
            except Exception:  # noqa: BLE001 - never serialize provider exception details
                checks.append(
                    CheckResult(
                        code="semantic.exception",
                        severity="p0",
                        passed=False,
                        message="semantic evaluator failed",
                    )
                )
        return EvaluationRecord.from_results(
            case_id=case.case_id,
            case_version=case.version,
            mode=mode,
            started_at=started_at,
            duration_ms=max(0, int((perf_counter() - started) * 1_000)),
            observation=observation,
            deterministic_checks=tuple(checks),
            semantic_scores=semantic_scores,
        )


def _failed_observation(case_id: str) -> EvaluationObservation:
    return EvaluationObservation(
        case_id=case_id,
        org_id=1,
        user_id=1,
        account_id=1,
        thread_id=1,
        turn_id=1,
        final_answer="Evaluation case execution failed.",
        terminal_states={"runner": "failed"},
    )


def _semantic_cost_cny(semantic: SemanticEvaluator | None) -> float | None:
    if semantic is None or not hasattr(semantic, "spent_cost_cny"):
        return None
    value = semantic.spent_cost_cny  # type: ignore[attr-defined]
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ValueError("semantic evaluator exposed an invalid spent_cost_cny")
    return float(value)
