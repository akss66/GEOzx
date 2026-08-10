from __future__ import annotations

import json
from pathlib import Path

from evals.models import EvaluationCase


def load_evaluation_cases(path: Path) -> tuple[EvaluationCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("evaluation case file must contain a JSON array")
    cases = tuple(EvaluationCase.model_validate(item) for item in payload)
    identities = [(case.case_id, case.version) for case in cases]
    if len(identities) != len(set(identities)):
        raise ValueError("evaluation case_id + version must be unique")
    return cases
