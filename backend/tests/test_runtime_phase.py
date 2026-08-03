from app.services.runtime_phase import normalize_runtime_phase


def test_normalizes_public_turn_phases_from_runtime_events() -> None:
    assert normalize_runtime_phase("brain.runtime.started", {}) == "understanding"
    assert normalize_runtime_phase(
        "brain.runtime.tool_started", {"tool_code": "account.data_context"}
    ) == "reading_data"
    assert normalize_runtime_phase("brain.runtime.subagent_started", {}) == "consulting_experts"
    assert normalize_runtime_phase("brain.runtime.critic_scored", {}) == "quality_review"
    assert normalize_runtime_phase("brain.runtime.approval_required", {}) == "waiting_approval"
    assert normalize_runtime_phase("brain.runtime.message_delta", {}) == "composing_artifact"
    assert normalize_runtime_phase("brain.runtime.completed", {}) == "completed"
    assert normalize_runtime_phase("brain.runtime.failed", {}) == "failed"


def test_explicit_public_turn_phase_wins_and_invalid_values_fail_closed() -> None:
    assert normalize_runtime_phase(
        "brain.runtime.tool_started", {"turn_phase": "quality_review"}
    ) == "quality_review"
    assert normalize_runtime_phase(
        "brain.runtime.tool_started", {"turn_phase": "internal_magic"}
    ) == "reading_data"

