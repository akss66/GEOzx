from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from evals.case_loader import load_evaluation_cases  # noqa: E402
from evals.deepeval_adapter import (  # noqa: E402
    DeepEvalSemanticEvaluator,
    DeepEvalUnavailable,
)
from evals.models import EvaluationCase, EvaluationObservation  # noqa: E402
from evals.reporting import write_report  # noqa: E402
from evals.runner import EvaluationRunner  # noqa: E402


class ReplayExecutor:
    def __init__(self, observations: tuple[EvaluationObservation, ...]) -> None:
        self._observations = {item.case_id: item for item in observations}
        if len(self._observations) != len(observations):
            raise ValueError("observation case_id values must be unique")

    async def execute(self, case: EvaluationCase) -> EvaluationObservation:
        try:
            return self._observations[case.case_id]
        except KeyError as exc:
            raise ValueError("one or more cases have no matching observation") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run redacted Main Agent V4.1 evaluations")
    parser.add_argument(
        "--mode",
        choices=("deterministic", "live-model"),
        default="deterministic",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=BACKEND / "evals/cases/account_analysis_v1.json",
    )
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--output-dir", type=Path, default=BACKEND / ".eval-results")
    parser.add_argument("--allow-external-output", action="store_true")
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--max-cost-cny", type=float)
    parser.add_argument("--usd-cny-rate", type=float)
    return parser


def _load_observations(path: Path) -> tuple[EvaluationObservation, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("observation file must contain a JSON array")
    return tuple(EvaluationObservation.model_validate(item) for item in payload)


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=False,
    )
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and len(commit) >= 4 else "local"


def _validated_output_dir(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Path:
    output_dir = args.output_dir.resolve()
    if not args.allow_external_output and not output_dir.is_relative_to(REPOSITORY.resolve()):
        parser.error("external output requires --allow-external-output")
    return output_dir


async def _run(args: argparse.Namespace, output_dir: Path) -> int:
    cases = load_evaluation_cases(args.cases.resolve())
    observations = _load_observations(args.observations.resolve())
    case_ids = {case.case_id for case in cases}
    observation_ids = {item.case_id for item in observations}
    if case_ids != observation_ids:
        raise ValueError("case and observation IDs must match exactly")

    semantic = None
    if args.mode == "live-model":
        semantic = DeepEvalSemanticEvaluator(
            max_cost_cny=args.max_cost_cny,
            usd_cny_rate=args.usd_cny_rate,
        )
    report = await EvaluationRunner(
        executor=ReplayExecutor(observations),
        semantic=semantic,
    ).run(
        cases,
        mode=args.mode,
        git_commit=_git_commit(),
    )
    report_path = write_report(report, output_dir)
    print(
        f"passed={report.passed_count} failed={report.failed_count} "
        f"semantic_cost_cny={report.semantic_cost_cny:.6f} report={report_path}"
    )
    return 0 if report.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.mode == "live-model":
        if not args.allow_model_calls:
            parser.error("live mode requires --allow-model-calls")
        if args.max_cost_cny is None or args.max_cost_cny <= 0:
            parser.error("live mode requires a positive --max-cost-cny")
        if args.usd_cny_rate is None or args.usd_cny_rate <= 0:
            parser.error("live mode requires a positive --usd-cny-rate")
    if args.observations is None:
        parser.error("--observations is required")
    output_dir = _validated_output_dir(parser, args)
    try:
        return asyncio.run(_run(args, output_dir))
    except (DeepEvalUnavailable, OSError, ValueError, json.JSONDecodeError):
        print("evaluation configuration or input is invalid", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
