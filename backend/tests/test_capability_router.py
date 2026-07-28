import pytest
from pydantic import BaseModel, ValidationError

from app.orchestrator.capability_router import SkillUnavailable, route_explicit_request
from app.orchestrator.skills.registry import SkillRegistry
from app.schemas.conversation import TurnExecutionMode, TurnRouteDecision
from app.schemas.skills import SkillDefinition


class AccountInspectionInput(BaseModel):
    pass


class AccountInspectionReport(BaseModel):
    summary: str


@pytest.fixture
def registry() -> SkillRegistry:
    return SkillRegistry(
        [
            SkillDefinition(
                code="account_inspection",
                version=1,
                name="账号体检",
                description="诊断当前账号",
                supported_platforms=frozenset({"douyin"}),
                input_model=AccountInspectionInput,
                output_model=AccountInspectionReport,
                expert_codes=("06-operator",),
                tool_codes=("account.profile",),
                risk_level="low",
                approval_policy="none",
                artifact_type="account_inspection_report",
            )
        ]
    )


def test_explicit_skill_launch_is_not_reclassified(registry: SkillRegistry) -> None:
    decision = route_explicit_request(
        requested_skill_code="account_inspection",
        platform="douyin",
        registry=registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode == TurnExecutionMode.SKILL
    assert decision.skill_code == "account_inspection"
    assert decision.requires_account_context is True
    assert decision.requires_operation_task is True


def test_account_skill_without_account_requests_clarification(registry: SkillRegistry) -> None:
    decision = route_explicit_request(
        requested_skill_code="account_inspection",
        platform="douyin",
        registry=registry,
        has_account=False,
    )

    assert decision is not None
    assert decision.mode == TurnExecutionMode.CLARIFY
    assert decision.missing_field == "account_id"
    assert decision.clarifying_question == "请先选择需要操作的账号。"


def test_unknown_explicit_skill_is_unavailable(registry: SkillRegistry) -> None:
    with pytest.raises(SkillUnavailable) as exc_info:
        route_explicit_request(
            requested_skill_code="not_registered",
            platform="douyin",
            registry=registry,
            has_account=True,
        )

    assert exc_info.value.code == "unknown_skill"
    assert exc_info.value.reason == "requested_skill_not_registered"


def test_platform_incompatible_explicit_skill_is_unavailable(registry: SkillRegistry) -> None:
    with pytest.raises(SkillUnavailable) as exc_info:
        route_explicit_request(
            requested_skill_code="account_inspection",
            platform="xiaohongshu",
            registry=registry,
            has_account=True,
        )

    assert exc_info.value.code == "unsupported_platform"
    assert exc_info.value.reason == "requested_skill_platform_incompatible"


def test_no_explicit_skill_defers_to_llm_classification(registry: SkillRegistry) -> None:
    assert (
        route_explicit_request(
            requested_skill_code=None,
            platform="douyin",
            registry=registry,
            has_account=True,
        )
        is None
    )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_route_decision_rejects_confidence_outside_unit_interval(confidence: float) -> None:
    with pytest.raises(ValidationError):
        TurnRouteDecision(
            mode=TurnExecutionMode.ANSWER,
            intent="answer",
            confidence=confidence,
            reason="test",
        )


@pytest.mark.parametrize(
    ("mode", "fields"),
    [
        (TurnExecutionMode.SKILL, {}),
        (TurnExecutionMode.CLARIFY, {}),
        (TurnExecutionMode.CLARIFY, {"missing_field": "account_id"}),
    ],
)
def test_route_decision_rejects_incomplete_mode_contract(
    mode: TurnExecutionMode, fields: dict[str, str]
) -> None:
    with pytest.raises(ValidationError):
        TurnRouteDecision(mode=mode, intent="test", confidence=1, reason="test", **fields)


@pytest.mark.parametrize(
    ("mode", "fields"),
    [
        (
            TurnExecutionMode.SKILL,
            {
                "skill_code": "account_inspection",
                "requires_account_context": False,
                "requires_operation_task": True,
            },
        ),
        (
            TurnExecutionMode.SKILL,
            {
                "skill_code": "account_inspection",
                "requires_account_context": True,
                "requires_operation_task": False,
            },
        ),
        (
            TurnExecutionMode.CLARIFY,
            {
                "missing_field": "account_id",
                "clarifying_question": "请选择账号。",
                "requires_operation_task": True,
            },
        ),
        (TurnExecutionMode.ANSWER, {"skill_code": "account_inspection"}),
        (TurnExecutionMode.QUERY, {"clarifying_question": "请选择账号。"}),
        (TurnExecutionMode.TASK, {"requires_operation_task": False}),
        (TurnExecutionMode.ACTION, {"requires_operation_task": False}),
    ],
)
def test_route_decision_rejects_mode_specific_invalid_state(
    mode: TurnExecutionMode, fields: dict[str, str | bool]
) -> None:
    with pytest.raises(ValidationError):
        TurnRouteDecision(mode=mode, intent="test", confidence=1, reason="test", **fields)


def test_query_route_can_keep_skill_and_account_context() -> None:
    decision = TurnRouteDecision(
        mode=TurnExecutionMode.QUERY,
        intent="account_data_query",
        confidence=1,
        reason="test",
        skill_code="account_inspection",
        requires_account_context=True,
    )

    assert decision.mode == TurnExecutionMode.QUERY
