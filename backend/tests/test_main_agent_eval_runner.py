from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import anyio
import pytest

from evals.models import (
    EvaluationCase,
    EvaluationExpectation,
    EvaluationObservation,
    SemanticScore,
)
from evals.reporting import write_report
from evals.runner import EvaluationRunner

BACKEND = Path(__file__).parents[1]
CLI = BACKEND / "scripts/run_main_agent_evals.py"


def _case(case_id: str) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        version="1.0.0",
        category="data_query",
        description=f"evaluate selected account for {case_id}",
        account_fixture="complete_30d",
        messages=("当前账号有数据吗？",),
        expectation=EvaluationExpectation(
            expected_mode="query",
            expected_skill_code="account_data_query",
            required_tools=("account.data_context",),
            forbidden_tools=("strategy.generate",),
            expected_answerability="full",
            required_claims=("data_exists",),
            forbidden_claims=("cross_account_data",),
            maximum_expert_invocations=0,
            maximum_retry_count=0,
            allowed_terminal_statuses=("completed",),
        ),
    )


def _matching_observation(
    case: EvaluationCase,
    *,
    foreign_evidence: bool = False,
) -> EvaluationObservation:
    account_id = 3
    return EvaluationObservation(
        case_id=case.case_id,
        org_id=1,
        user_id=2,
        account_id=account_id,
        thread_id=4,
        turn_id=5,
        route_mode="query",
        route_skill_code="account_data_query",
        tool_calls=({"tool_code": "account.data_context", "retry_count": 0},),
        evidence_refs=(
            {
                "account_id": 4 if foreign_evidence else account_id,
                "metric_code": "play",
                "value": 700,
                "unit": "count",
            },
        ),
        answer_payload={
            "answerability": "full",
            "claims": ["data_exists"],
            "key_facts": [],
            "api_key": "API_KEY_SECRET",
            "raw_prompt": "RAW_PROMPT_SECRET",
        },
        final_answer="当前账号已有确认数据。",
        terminal_states={"turn": "completed", "run": "completed", "skill": "completed"},
        timings_ms={"total": 100},
        model_metadata={"authorization": "AUTH_SECRET", "model": "test-model"},
    )


class FakeExecutor:
    def __init__(self, *, foreign_case: str | None = None) -> None:
        self.foreign_case = foreign_case
        self.executed: list[str] = []

    async def execute(self, case: EvaluationCase) -> EvaluationObservation:
        self.executed.append(case.case_id)
        return _matching_observation(
            case,
            foreign_evidence=case.case_id == self.foreign_case,
        )


class FailingExecutor:
    async def execute(self, case: EvaluationCase) -> EvaluationObservation:
        raise RuntimeError(f"API_KEY_SECRET failed {case.case_id}")


class SemanticEvaluatorSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def evaluate(
        self,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> tuple[SemanticScore, ...]:
        del observation
        self.calls.append(case.case_id)
        return (
            SemanticScore.from_score(
                metric="faithfulness",
                score=0.9,
                threshold=0.8,
                reason="grounded",
            ),
        )


class RaisingCostEvaluator:
    spent_cost_cny = 0.12

    async def evaluate(
        self,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> tuple[SemanticScore, ...]:
        del case, observation
        raise RuntimeError("provider response must be redacted")


@pytest.mark.asyncio
async def test_runner_marks_batch_failed_when_one_p0_check_fails() -> None:
    runner = EvaluationRunner(executor=FakeExecutor(foreign_case="bad-case"))

    report = await runner.run(
        (_case("ok-case"), _case("bad-case")),
        mode="deterministic",
        git_commit="abc1234",
    )

    assert report.passed is False
    assert report.passed_count == 1
    assert report.failed_count == 1
    assert report.records[1].failure_reasons == ("scope.account", "evidence.account")


@pytest.mark.asyncio
async def test_runner_preserves_stable_input_order() -> None:
    executor = FakeExecutor()
    cases = (_case("case-three"), _case("case-one"), _case("case-two"))

    report = await EvaluationRunner(executor=executor).run(
        cases,
        mode="deterministic",
        git_commit="abc1234",
    )

    assert executor.executed == [case.case_id for case in cases]
    assert [record.case_id for record in report.records] == [case.case_id for case in cases]


@pytest.mark.asyncio
async def test_runner_converts_executor_exception_to_redacted_failed_record() -> None:
    report = await EvaluationRunner(executor=FailingExecutor()).run(
        (_case("error-case"),),
        mode="deterministic",
        git_commit="abc1234",
    )

    assert report.passed is False
    assert report.records[0].failure_reasons == ("runner.exception",)
    assert "API_KEY_SECRET" not in report.model_dump_json()


def test_live_mode_requires_explicit_model_call_consent() -> None:
    result = subprocess.run(
        [sys.executable, str(CLI), "--mode", "live-model"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--allow-model-calls" in result.stderr


def test_live_mode_requires_explicit_currency_conversion_rate() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--mode",
            "live-model",
            "--allow-model-calls",
            "--max-cost-cny",
            "2",
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--usd-cny-rate" in result.stderr


def test_deterministic_mode_writes_a_local_json_report(tmp_path: Path) -> None:
    case = _case("cli-case")
    cases = tmp_path / "cases.json"
    observations = tmp_path / "observations.json"
    cases.write_text(
        json.dumps([case.model_dump(mode="json")], ensure_ascii=False),
        encoding="utf-8",
    )
    observations.write_text(
        json.dumps([_matching_observation(case).model_dump(mode="json")], ensure_ascii=False),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--mode",
            "deterministic",
            "--cases",
            str(cases),
            "--observations",
            str(observations),
            "--output-dir",
            str(tmp_path),
            "--allow-external-output",
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = next(tmp_path.glob("main-agent-eval-*.json"))
    assert json.loads(report.read_text(encoding="utf-8"))["records"]


@pytest.mark.asyncio
async def test_deterministic_mode_never_calls_semantic_evaluator() -> None:
    semantic = SemanticEvaluatorSpy()

    report = await EvaluationRunner(executor=FakeExecutor(), semantic=semantic).run(
        (_case("deterministic-case"),),
        mode="deterministic",
        git_commit="abc1234",
    )

    assert semantic.calls == []
    assert report.records[0].semantic_scores == ()
    assert report.semantic_average is None


@pytest.mark.asyncio
async def test_live_mode_collects_semantic_scores() -> None:
    semantic = SemanticEvaluatorSpy()

    report = await EvaluationRunner(executor=FakeExecutor(), semantic=semantic).run(
        (_case("live-case"),),
        mode="live-model",
        git_commit="abc1234",
    )

    assert semantic.calls == ["live-case"]
    assert report.semantic_average == 0.9
    assert report.passed is True


@pytest.mark.asyncio
async def test_live_mode_preserves_known_cost_when_semantic_evaluation_fails() -> None:
    report = await EvaluationRunner(
        executor=FakeExecutor(),
        semantic=RaisingCostEvaluator(),
    ).run(
        (_case("failed-live-case"),),
        mode="live-model",
        git_commit="abc1234",
    )

    assert report.passed is False
    assert report.records[0].failure_reasons == ("semantic.exception",)
    assert report.semantic_cost_cny == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_report_writer_is_atomic_and_removes_sensitive_nested_fields(
    tmp_path: Path,
) -> None:
    report = await EvaluationRunner(executor=FakeExecutor()).run(
        (_case("safe-report"),),
        mode="deterministic",
        git_commit="abc1234",
    )

    output = write_report(report, tmp_path)
    serialized = output.read_text(encoding="utf-8")
    payload = json.loads(serialized)

    assert output.name.startswith("main-agent-eval-")
    assert output.name.endswith("-abc1234.json")
    assert payload["records"]
    temporary_files = await anyio.to_thread.run_sync(lambda: list(tmp_path.glob("*.tmp")))
    assert not temporary_files
    for forbidden in ("authorization", "api_key", "raw_prompt", "API_KEY_SECRET"):
        assert forbidden not in serialized
