from __future__ import annotations

import importlib
import inspect
import json
import os
from collections.abc import Callable, Mapping
from typing import Any

import anyio

from evals.models import EvaluationCase, EvaluationObservation, SemanticScore

_METRIC_ORDER = (
    "task_completion",
    "answer_relevancy",
    "faithfulness",
    "turn_faithfulness",
    "role_adherence",
    "actionability",
)
_DEFAULT_THRESHOLD = 0.8
_CHATBOT_ROLE = (
    "抖音账号运营分析助手：只使用当前账号已确认导入的数据，明确证据边界，"
    "给出可验证的运营建议，不承诺结果，不跨账号取数。"
)

MetricFactory = Callable[
    [str, EvaluationCase, EvaluationObservation],
    Mapping[str, tuple[object, object]],
]


class DeepEvalUnavailable(RuntimeError):
    """Raised when optional semantic evaluation cannot run safely."""


class DeepEvalSemanticEvaluator:
    def __init__(
        self,
        *,
        model: str | None = None,
        metric_factory: MetricFactory | None = None,
    ) -> None:
        self._model = (model or os.getenv("MAIN_AGENT_EVAL_JUDGE_MODEL") or "").strip()
        self._metric_factory = metric_factory or _default_metric_factory

    async def evaluate(
        self,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> tuple[SemanticScore, ...]:
        if not self._model:
            raise DeepEvalUnavailable(
                "live semantic evaluation requires MAIN_AGENT_EVAL_JUDGE_MODEL"
            )
        executions = self._metric_factory(self._model, case, observation)
        missing = [name for name in _METRIC_ORDER if name not in executions]
        if missing:
            raise DeepEvalUnavailable(
                f"semantic metric factory is missing required metrics: {', '.join(missing)}"
            )

        scores: list[SemanticScore] = []
        for name in _METRIC_ORDER:
            metric, test_case = executions[name]
            await _measure(metric, test_case)
            raw_score = getattr(metric, "score", None)
            if isinstance(raw_score, bool) or not isinstance(raw_score, int | float):
                raise DeepEvalUnavailable(f"semantic metric {name} returned no numeric score")
            score = min(1.0, max(0.0, float(raw_score)))
            raw_threshold = getattr(metric, "threshold", _DEFAULT_THRESHOLD)
            threshold = (
                _DEFAULT_THRESHOLD
                if raw_threshold is None
                else min(1.0, max(0.0, float(raw_threshold)))
            )
            scores.append(
                SemanticScore.from_score(
                    metric=name,
                    score=score,
                    threshold=threshold,
                    reason="judge completed; provider rationale omitted from persisted report",
                )
            )
        return tuple(scores)


async def _measure(metric: object, test_case: object) -> None:
    async_measure = getattr(metric, "a_measure", None)
    if callable(async_measure):
        result = async_measure(test_case)
        if inspect.isawaitable(result):
            await result
            return
    measure = getattr(metric, "measure", None)
    if not callable(measure):
        raise DeepEvalUnavailable("semantic metric does not expose measure or a_measure")
    await anyio.to_thread.run_sync(measure, test_case)


def _required_attribute(module: object, name: str) -> Any:
    value = getattr(module, name, None)
    if value is None:
        raise DeepEvalUnavailable(
            f"installed DeepEval version does not expose required API: {name}"
        )
    return value


def _default_metric_factory(
    model: str,
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> Mapping[str, tuple[object, object]]:
    api_key = (os.getenv("MAIN_AGENT_EVAL_JUDGE_API_KEY") or "").strip()
    if not api_key:
        raise DeepEvalUnavailable(
            "live semantic evaluation requires MAIN_AGENT_EVAL_JUDGE_API_KEY"
        )
    base_url = (os.getenv("MAIN_AGENT_EVAL_JUDGE_BASE_URL") or "").strip() or None
    os.environ["DEEPEVAL_DISABLE_DOTENV"] = "1"
    try:
        metrics = importlib.import_module("deepeval.metrics")
        models = importlib.import_module("deepeval.models")
        test_cases = importlib.import_module("deepeval.test_case")
    except (ImportError, ModuleNotFoundError) as exc:
        raise DeepEvalUnavailable(
            "DeepEval is unavailable; install the backend eval extra"
        ) from exc

    LLMTestCase = _required_attribute(test_cases, "LLMTestCase")
    ConversationalTestCase = _required_attribute(test_cases, "ConversationalTestCase")
    Turn = _required_attribute(test_cases, "Turn")
    SingleTurnParams = _required_attribute(test_cases, "SingleTurnParams")
    GEval = _required_attribute(metrics, "GEval")
    AnswerRelevancyMetric = _required_attribute(metrics, "AnswerRelevancyMetric")
    FaithfulnessMetric = _required_attribute(metrics, "FaithfulnessMetric")
    TurnFaithfulnessMetric = _required_attribute(metrics, "TurnFaithfulnessMetric")
    RoleAdherenceMetric = _required_attribute(metrics, "RoleAdherenceMetric")
    GPTModel = _required_attribute(models, "GPTModel")
    judge = GPTModel(model=model, api_key=api_key, base_url=base_url)

    evidence_context = [
        json.dumps(
            list(observation.evidence_refs),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    ]
    user_input = "\n".join(case.messages)
    single_case = LLMTestCase(
        input=user_input,
        actual_output=observation.final_answer,
        retrieval_context=evidence_context,
    )
    turns = [Turn(role="user", content=message) for message in case.messages]
    turns.append(
        Turn(
            role="assistant",
            content=observation.final_answer,
            retrieval_context=evidence_context,
        )
    )
    conversation_case = ConversationalTestCase(
        chatbot_role=_CHATBOT_ROLE,
        turns=turns,
    )
    input_param = _required_attribute(SingleTurnParams, "INPUT")
    output_param = _required_attribute(SingleTurnParams, "ACTUAL_OUTPUT")

    common = {"threshold": _DEFAULT_THRESHOLD, "model": judge}
    return {
        "task_completion": (
            GEval(
                name="Task Completion",
                criteria=(
                    "Judge whether the output directly completes the user's account-analysis "
                    "request within the stated data and instruction boundaries."
                ),
                evaluation_params=[input_param, output_param],
                **common,
            ),
            single_case,
        ),
        "answer_relevancy": (AnswerRelevancyMetric(**common), single_case),
        "faithfulness": (FaithfulnessMetric(**common), single_case),
        "turn_faithfulness": (TurnFaithfulnessMetric(**common), conversation_case),
        "role_adherence": (RoleAdherenceMetric(**common), conversation_case),
        "actionability": (
            GEval(
                name="Actionability",
                criteria=(
                    "Recommendations must include a concrete action, rationale, validation "
                    "metric, and observation period; factual-only requests must not invent advice."
                ),
                evaluation_params=[input_param, output_param],
                **common,
            ),
            single_case,
        ),
    }


__all__ = ["DeepEvalSemanticEvaluator", "DeepEvalUnavailable"]
