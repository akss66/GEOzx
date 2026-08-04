"""Strict internal DTOs for revision and checkpoint persistence boundaries."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

HexDigest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]

_FORBIDDEN_KEYS = frozenset(
    {
        "secret",
        "password",
        "token",
        "auth",
        "authorization",
        "access_token",
        "refresh_token",
        "api_key",
        "prompt",
        "full_prompt",
        "system_prompt",
        "developer_prompt",
        "provider_raw_response",
        "raw_response",
        "file_path",
        "filesystem_path",
        "sql",
    }
)
_MAX_ENVELOPE_BYTES = 256 * 1024


def _normalized_persistence_key(key: str) -> str:
    normalized = unicodedata.normalize("NFC", key)
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", normalized)
    return re.sub(r"[^0-9A-Za-z]+", "_", separated).strip("_").casefold()


def _validate_json_column_size(value: Any, *, label: str) -> None:
    from app.services.checkpoint_hashing import canonical_json_bytes

    if len(canonical_json_bytes(value)) > _MAX_ENVELOPE_BYTES:
        raise ValueError(f"{label} exceeds 256 KiB")


def _validate_persistence_value(value: Any, *, path: str = "data") -> None:
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{path} contains a datetime without timezone")
        return
    if isinstance(value, date):
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_persistence_value(item, path=f"{path}[{index}]")
        return
    if type(value) is dict:
        normalized: set[str] = set()
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path} contains a non-string key")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise ValueError(f"{path} contains duplicate keys after NFC normalization")
            normalized.add(normalized_key)
            safe_key = _normalized_persistence_key(normalized_key)
            key_parts = frozenset(part for part in safe_key.split("_") if part)
            if safe_key in _FORBIDDEN_KEYS or key_parts.intersection(
                {
                    "secret",
                    "password",
                    "token",
                    "auth",
                    "authorization",
                    "prompt",
                    "sql",
                    "path",
                }
            ) or safe_key.endswith(
                ("_raw_response", "_api_key")
            ):
                raise ValueError(f"forbidden persistence key: {key}")
            _validate_persistence_value(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} contains unsupported value type: {type(value).__name__}")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class StageDataEnvelope(_StrictModel):
    schema_version: Annotated[str, Field(min_length=1, max_length=120)]
    data: dict[str, Any]

    @field_validator("data", mode="before")
    @classmethod
    def _validate_data(cls, value: Any) -> Any:
        if type(value) is not dict:
            raise ValueError("stage data must be an object envelope")
        _validate_persistence_value(value)
        return value

    @model_validator(mode="after")
    def _validate_encoded_size(self) -> StageDataEnvelope:
        _validate_json_column_size(
            self.model_dump(mode="python"), label="stage data envelope"
        )
        return self


class ArtifactRef(_StrictModel):
    deliverable_id: PositiveStrictInt
    artifact_type: Annotated[str, Field(min_length=1, max_length=120)]
    version: PositiveStrictInt
    payload_hash: HexDigest
    account_id: PositiveStrictInt


class EvidenceRef(_StrictModel):
    kind: Annotated[str, Field(min_length=1, max_length=120)]
    source_id: PositiveStrictInt | Annotated[str, Field(min_length=1, max_length=160)]
    source_version: Annotated[str, Field(min_length=1, max_length=120)]
    content_hash: HexDigest
    account_id: PositiveStrictInt


class FreshnessStamp(_StrictModel):
    policy_key: Annotated[str, Field(min_length=1, max_length=120)]
    watermark_hash: HexDigest
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def _aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("freshness expiry must include a timezone")
        return value


class CompletedStageDraft(_StrictModel):
    step_key: Annotated[str, Field(min_length=1, max_length=160)]
    input: StageDataEnvelope
    output: StageDataEnvelope
    artifact_refs: tuple[ArtifactRef, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    langgraph_checkpoint_id: Annotated[str, Field(min_length=1, max_length=160)] | None = None

    @model_validator(mode="after")
    def _validate_reference_sizes(self) -> CompletedStageDraft:
        _validate_json_column_size(
            [item.model_dump(mode="python") for item in self.artifact_refs],
            label="artifact reference array",
        )
        _validate_json_column_size(
            [item.model_dump(mode="python") for item in self.evidence_refs],
            label="evidence reference array",
        )
        return self


class StageReuseBinding(_StrictModel):
    step_key: str
    source_checkpoint_id: PositiveStrictInt
    checkpoint_id: PositiveStrictInt
    output: StageDataEnvelope


class ExpectedStageInputs(_StrictModel):
    values: dict[Annotated[str, Field(min_length=1, max_length=160)], StageDataEnvelope]


class PartialExecution(_StrictModel):
    kind: Literal["partial"] = "partial"
    execute_steps: tuple[str, ...]
    reused: tuple[StageReuseBinding, ...]
    hydrated_outputs: dict[str, StageDataEnvelope]
    plan_hash: HexDigest


class FullRecompute(_StrictModel):
    kind: Literal["full_recompute"] = "full_recompute"
    reason: str
    execute_steps: tuple[str, ...]
    plan_hash: HexDigest


class ManualReconciliation(_StrictModel):
    kind: Literal["manual_reconciliation"] = "manual_reconciliation"
    reason: str
    blocking_receipt_ids: tuple[PositiveStrictInt, ...] = ()
    plan_hash: HexDigest


class NoRevisionRequired(_StrictModel):
    kind: Literal["no_revision_required"] = "no_revision_required"
    reason: Literal["empty_diff"] = "empty_diff"


class RevisionResolution(_StrictModel):
    mode: Literal["partial", "full_recompute", "manual_reconciliation"]
    reason: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    execute_steps: tuple[Annotated[str, Field(min_length=1, max_length=160)], ...]
    reused_steps: tuple[Annotated[str, Field(min_length=1, max_length=160)], ...]
    source_checkpoint_ids: tuple[PositiveStrictInt, ...] = ()
    blocking_receipt_ids: tuple[PositiveStrictInt, ...] = ()
    plan_hash: HexDigest

    @model_validator(mode="after")
    def _reason_matches_mode(self) -> RevisionResolution:
        if self.mode == "partial" and self.reason is not None:
            raise ValueError("partial resolution cannot have a fallback reason")
        if self.mode != "partial" and self.reason is None:
            raise ValueError("non-partial resolution requires a stable reason")
        return self


class CheckpointWriteResult(_StrictModel):
    checkpoint_id: PositiveStrictInt
    created: bool


class ResolvedStageOutput(_StrictModel):
    checkpoint_id: PositiveStrictInt
    source_checkpoint_id: PositiveStrictInt | None = None
    output: StageDataEnvelope


__all__ = [
    "ArtifactRef",
    "CheckpointWriteResult",
    "CompletedStageDraft",
    "EvidenceRef",
    "ExpectedStageInputs",
    "FreshnessStamp",
    "FullRecompute",
    "HexDigest",
    "ManualReconciliation",
    "NoRevisionRequired",
    "PartialExecution",
    "ResolvedStageOutput",
    "RevisionResolution",
    "StageDataEnvelope",
    "StageReuseBinding",
]
