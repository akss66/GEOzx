"""Strict checkpoint DTO and canonical identity tests."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.orchestrator.checkpoint_graph_contracts import (
    CheckpointGraphContract,
    require_checkpoint_graph_contract,
)
from app.schemas.run_revision import ArtifactRef, CompletedStageDraft, StageDataEnvelope
from app.services.checkpoint_hashing import (
    CanonicalJsonError,
    canonical_json_bytes,
    canonical_json_sha256,
    revision_plan_hash,
    stage_contract_hash,
    stage_input_hash,
    stage_output_hash,
)


def test_canonical_hash_is_key_order_independent_and_domain_separated() -> None:
    left = {"alpha": 1, "nested": {"beta": "value"}}
    right = {"nested": {"beta": "value"}, "alpha": 1}

    assert canonical_json_sha256(domain="same/v1", value=left) == canonical_json_sha256(
        domain="same/v1", value=right
    )
    assert canonical_json_sha256(domain="first/v1", value=left) != canonical_json_sha256(
        domain="second/v1", value=left
    )


def test_canonical_json_normalizes_unicode_and_rejects_duplicate_nfc_keys() -> None:
    assert canonical_json_bytes({"name": "caf\u00e9"}) == canonical_json_bytes(
        {"name": "cafe\u0301"}
    )

    with pytest.raises(CanonicalJsonError, match="duplicate key"):
        canonical_json_bytes({"\u00e9": 1, "e\u0301": 2})


def test_canonical_json_normalizes_aware_datetimes_to_utc() -> None:
    utc_value = datetime(2026, 8, 4, 4, 30, tzinfo=UTC)
    offset_value = utc_value.astimezone(timezone(timedelta(hours=8)))

    assert canonical_json_bytes({"at": utc_value}) == canonical_json_bytes({"at": offset_value})
    with pytest.raises(CanonicalJsonError, match="timezone"):
        canonical_json_bytes({"at": utc_value.replace(tzinfo=None)})


@pytest.mark.parametrize(
    "value",
    [
        {"bad": math.nan},
        {"bad": math.inf},
        {1: "bad key"},
        {"bad": b"bytes"},
        {"bad": {"set"}},
        {"bad": ("tuple",)},
    ],
)
def test_canonical_json_rejects_non_json_values(value: object) -> None:
    with pytest.raises(CanonicalJsonError):
        canonical_json_bytes(value)


def test_stage_envelope_is_strict_bounded_and_forbids_sensitive_payloads() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        StageDataEnvelope(schema_version="input/v1", data={}, extra_field=True)
    with pytest.raises(ValidationError):
        ArtifactRef(
            deliverable_id=True,
            artifact_type="report",
            version=1,
            payload_hash="a" * 64,
            account_id=1,
        )
    with pytest.raises(ValidationError, match="256 KiB"):
        StageDataEnvelope(schema_version="input/v1", data={"body": "x" * (256 * 1024)})

    for forbidden in ("secret", "full_prompt", "provider_raw_response", "file_path", "sql"):
        with pytest.raises(ValidationError, match="forbidden persistence key"):
            StageDataEnvelope(schema_version="input/v1", data={forbidden: "unsafe"})


def test_hash_fields_are_lowercase_hex_and_caller_cannot_forge_derived_fields() -> None:
    with pytest.raises(ValidationError):
        ArtifactRef(
            deliverable_id=1,
            artifact_type="report",
            version=1,
            payload_hash="A" * 64,
            account_id=1,
        )
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CompletedStageDraft(
            step_key="topic_planning",
            input=StageDataEnvelope(schema_version="input/v1", data={}),
            output=StageDataEnvelope(schema_version="output/v1", data={"topic_plan": {}}),
            freshness_expires_at="2026-08-04T00:00:00Z",
        )


def test_stage_hashes_are_semantic_and_contract_hash_covers_contract_identity() -> None:
    envelope = StageDataEnvelope(schema_version="input/v1", data={"goal": "增长"})
    assert stage_input_hash(envelope) != stage_output_hash(envelope)
    assert revision_plan_hash({"mode": "partial"}) != stage_input_hash(envelope)

    contract = require_checkpoint_graph_contract("operation_iteration", 1)
    step = contract.steps[2]
    baseline = stage_contract_hash(contract=contract, step=step)
    changed_step = replace(step, output_schema_version="topic-output/v2")
    changed_contract = CheckpointGraphContract(
        skill_code=contract.skill_code,
        skill_version=contract.skill_version,
        graph_version=contract.graph_version,
        steps=tuple(changed_step if item.key == step.key else item for item in contract.steps),
    )

    assert stage_contract_hash(contract=changed_contract, step=changed_step) != baseline
