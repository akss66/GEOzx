"""Canonical, domain-separated identities for checkpoint persistence."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from datetime import UTC, date, datetime
from typing import Any

from app.orchestrator.checkpoint_graph_contracts import (
    CheckpointGraphContract,
    CheckpointStepSpec,
)
from app.schemas.run_revision import StageDataEnvelope


class CanonicalJsonError(ValueError):
    pass


def _normalize(value: Any, *, path: str = "$") -> Any:
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise CanonicalJsonError(f"Non-finite number at {path}")
        return value
    if type(value) is str:
        return unicodedata.normalize("NFC", value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalJsonError(f"Datetime timezone is required at {path}")
        utc_value = value.astimezone(UTC)
        return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if type(value) is list:
        return [_normalize(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    if type(value) is dict:
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise CanonicalJsonError(f"Non-string key at {path}")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise CanonicalJsonError(f"NFC duplicate key at {path}: {normalized_key}")
            normalized[normalized_key] = _normalize(item, path=f"{path}.{normalized_key}")
        return normalized
    raise CanonicalJsonError(f"Unsupported canonical JSON type at {path}: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            _normalize(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        if isinstance(error, CanonicalJsonError):
            raise
        raise CanonicalJsonError("Value is not canonical JSON") from error


def canonical_json_sha256(*, domain: str, value: Any) -> str:
    if type(domain) is not str or not domain:
        raise CanonicalJsonError("Hash domain is required")
    payload = {"domain": unicodedata.normalize("NFC", domain), "value": value}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def stage_contract_hash(*, contract: CheckpointGraphContract, step: CheckpointStepSpec) -> str:
    if not any(candidate is step or candidate == step for candidate in contract.steps):
        raise CanonicalJsonError("Step is not part of checkpoint graph contract")
    return canonical_json_sha256(
        domain="stage-contract/v1",
        value={
            "skill_code": contract.skill_code,
            "skill_version": contract.skill_version,
            "graph_version": contract.graph_version,
            "step_key": step.key,
            "consumes_constraints": sorted(path.value for path in step.consumes_constraints),
            "consumes_outputs": sorted(step.consumes_outputs),
            "produces_outputs": sorted(step.produces_outputs),
            "reuse_policy": step.reuse_policy,
            "side_effect_level": step.side_effect_level,
            "input_schema_version": step.input_schema_version,
            "output_schema_version": step.output_schema_version,
            "input_projection_key": step.input_projection_key,
            "freshness_policy_key": step.freshness_policy_key,
            "executor_owner": step.executor_owner,
            "executor_boundary_key": step.executor_boundary_key,
        },
    )


def stage_input_hash(value: StageDataEnvelope) -> str:
    return canonical_json_sha256(domain="stage-input/v1", value=value.model_dump(mode="python"))


def stage_output_hash(value: StageDataEnvelope) -> str:
    return canonical_json_sha256(domain="stage-output/v1", value=value.model_dump(mode="python"))


def revision_plan_hash(value: Any) -> str:
    return canonical_json_sha256(domain="revision-plan/v1", value=value)


__all__ = [
    "CanonicalJsonError",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "revision_plan_hash",
    "stage_contract_hash",
    "stage_input_hash",
    "stage_output_hash",
]
