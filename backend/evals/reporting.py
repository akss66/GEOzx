from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.models import EvaluationBatchReport

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "credentials",
        "error_detail",
        "password",
        "provider_body",
        "provider_response",
        "raw_prompt",
        "secret",
    }
)


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower()
    return normalized in _SENSITIVE_KEYS or normalized.endswith("_secret")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact(item)
            for key, item in value.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def write_report(report: EvaluationBatchReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    commit = "".join(character for character in report.git_commit if character.isalnum())[:12]
    if not commit:
        commit = "unknown"
    output = output_dir / f"main-agent-eval-{timestamp}-{commit}.json"
    temporary = output.with_suffix(".json.tmp")
    payload = _redact(report.model_dump(mode="json"))
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    try:
        temporary.write_text(serialized + "\n", encoding="utf-8")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
