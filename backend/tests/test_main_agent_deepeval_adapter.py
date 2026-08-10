from __future__ import annotations

import importlib
import os
import sys
from dataclasses import dataclass

import pytest

from evals.models import EvaluationCase, EvaluationExpectation, EvaluationObservation


def _case() -> EvaluationCase:
    return EvaluationCase(
        case_id="semantic-analysis-01",
        version="1.0.0",
        category="diagnosis_and_advice",
        description="evaluate one grounded account analysis answer",
        account_fixture="complete_30d",
        messages=("分析最近30天账号表现", "只给三个可验证建议"),
        expectation=EvaluationExpectation(
            expected_mode="skill",
            expected_skill_code="account_data_analysis",
            required_tools=("account.metrics_analysis",),
            expected_answerability="full",
            required_claims=("recommendations_max_3",),
        ),
    )


def _observation() -> EvaluationObservation:
    return EvaluationObservation(
        case_id="semantic-analysis-01",
        org_id=1,
        user_id=2,
        account_id=3,
        thread_id=4,
        turn_id=5,
        route_mode="skill",
        route_skill_code="account_data_analysis",
        evidence_refs=(
            {
                "account_id": 3,
                "metric_code": "play",
                "value": 700,
                "unit": "count",
            },
        ),
        answer_payload={"claims": ["recommendations_max_3"]},
        final_answer="播放量为700。下批测试前三秒开头，观察7天播放量。",
        terminal_states={"turn": "completed", "run": "completed"},
    )


@dataclass
class FakeMetric:
    score: float
    reason: str = "RAW_PROMPT_SECRET should never leave the adapter"
    threshold: float = 0.8
    measured: bool = False

    async def a_measure(self, test_case: object) -> None:
        assert test_case is not None
        self.measured = True


def _fake_metrics(_model, _case, _observation):
    names = (
        "task_completion",
        "answer_relevancy",
        "faithfulness",
        "turn_faithfulness",
        "role_adherence",
        "actionability",
    )
    return {name: (FakeMetric(0.9), object()) for name in names}


def test_importing_eval_package_does_not_import_deepeval(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "deepeval", None)

    module = importlib.reload(importlib.import_module("evals"))

    assert hasattr(module, "EvaluationCase")


@pytest.mark.asyncio
async def test_adapter_maps_metric_results_without_leaking_prompts() -> None:
    from evals.deepeval_adapter import DeepEvalSemanticEvaluator

    adapter = DeepEvalSemanticEvaluator(model="test-judge", metric_factory=_fake_metrics)

    scores = await adapter.evaluate(_case(), _observation())

    assert {score.metric for score in scores} == {
        "task_completion",
        "answer_relevancy",
        "faithfulness",
        "turn_faithfulness",
        "role_adherence",
        "actionability",
    }
    assert all(score.score == 0.9 for score in scores)
    assert all(score.passed for score in scores)
    assert "RAW_PROMPT_SECRET" not in " ".join(score.reason for score in scores)


@pytest.mark.asyncio
async def test_adapter_requires_explicit_live_model_configuration(monkeypatch) -> None:
    from evals.deepeval_adapter import DeepEvalSemanticEvaluator, DeepEvalUnavailable

    monkeypatch.delenv("MAIN_AGENT_EVAL_JUDGE_MODEL", raising=False)

    with pytest.raises(DeepEvalUnavailable, match="MAIN_AGENT_EVAL_JUDGE_MODEL"):
        await DeepEvalSemanticEvaluator().evaluate(_case(), _observation())


@pytest.mark.asyncio
async def test_default_adapter_requires_explicit_judge_api_key(monkeypatch) -> None:
    from evals.deepeval_adapter import DeepEvalSemanticEvaluator, DeepEvalUnavailable

    monkeypatch.delenv("MAIN_AGENT_EVAL_JUDGE_API_KEY", raising=False)

    with pytest.raises(DeepEvalUnavailable, match="MAIN_AGENT_EVAL_JUDGE_API_KEY"):
        await DeepEvalSemanticEvaluator(model="gpt-4.1").evaluate(
            _case(),
            _observation(),
        )


def test_default_factory_disables_dotenv_before_import(monkeypatch) -> None:
    from evals.deepeval_adapter import DeepEvalUnavailable, _default_metric_factory

    monkeypatch.delenv("DEEPEVAL_DISABLE_DOTENV", raising=False)
    monkeypatch.setenv("MAIN_AGENT_EVAL_JUDGE_API_KEY", "test-key")
    real_import = importlib.import_module

    def guarded_import(name: str):
        if name.startswith("deepeval"):
            assert os.environ["DEEPEVAL_DISABLE_DOTENV"] == "1"
            raise ModuleNotFoundError("deepeval intentionally unavailable")
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", guarded_import)

    with pytest.raises(DeepEvalUnavailable, match="eval extra"):
        _default_metric_factory("test-judge", _case(), _observation())
