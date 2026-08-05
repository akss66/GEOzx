import pytest
from pydantic import BaseModel, ValidationError

from app.orchestrator import capability_router
from app.orchestrator.capability_router import SkillUnavailable, route_explicit_request
from app.orchestrator.skills.registry import SkillRegistry
from app.orchestrator.skills.registry import skill_registry as production_skill_registry
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
    assert decision.skill_code == "account_inspection"
    assert decision.requires_account_context is True
    assert decision.requires_operation_task is False
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


@pytest.mark.parametrize(
    ("message", "expected_mode"),
    [
        ("你好", TurnExecutionMode.ANSWER),
        ("你是谁？", TurnExecutionMode.ANSWER),
        ("你能做什么？", TurnExecutionMode.ANSWER),
        ("查询当前账号最近30天的数据", TurnExecutionMode.QUERY),
        ("只查询当前账号近30天数据", TurnExecutionMode.QUERY),
        ("我现在的账号有数据吗？", TurnExecutionMode.QUERY),
        ("最近30天播放量是多少？", TurnExecutionMode.QUERY),
        ("看看当前账号本周的点赞", TurnExecutionMode.QUERY),
        ("你能查询当前账号数据吗？", TurnExecutionMode.ANSWER),
    ],
)
def test_deterministic_request_routes_clear_safe_requests(
    registry: SkillRegistry,
    message: str,
    expected_mode: TurnExecutionMode,
) -> None:
    """Catches removal of the high-confidence task-free routing branches."""

    decision = capability_router.route_deterministic_request(
        message,
        platform="douyin",
        registry=registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is expected_mode
    assert decision.requires_operation_task is False


def test_deterministic_request_routes_account_inspection_to_published_skill(
    registry: SkillRegistry,
) -> None:
    """Catches an account-inspection request being sent to model classification."""

    decision = capability_router.route_deterministic_request(
        "给当前账号做一次体检",
        platform="douyin",
        registry=registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.SKILL
    assert decision.skill_code == "account_inspection"
    assert decision.requires_operation_task is True


@pytest.mark.parametrize(
    "message",
    [
        "我现在账号有数据吗？",
        "数据更新到哪一天？",
        "现在有哪些指标？",
    ],
)
def test_presence_questions_keep_the_fast_query_route(message: str) -> None:
    decision = capability_router.route_deterministic_request(
        message,
        platform="douyin",
        registry=production_skill_registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.QUERY
    assert decision.skill_code == "account_data_query"


@pytest.mark.parametrize(
    "message",
    [
        "最近30天账号表现怎么样？",
        "播放量从什么时候开始下降？",
        "哪个指标变化最大？",
        "表现最差的5条作品是什么？",
    ],
)
def test_analysis_questions_route_to_typed_analysis_skill(message: str) -> None:
    decision = capability_router.route_deterministic_request(
        message,
        platform="douyin",
        registry=production_skill_registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.SKILL
    assert decision.skill_code == "account_data_analysis"


def test_explicit_one_click_inspection_stays_account_inspection() -> None:
    decision = capability_router.route_deterministic_request(
        "给我做一次一键账号体检",
        platform="douyin",
        registry=production_skill_registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.skill_code == "account_inspection"


def test_analysis_without_account_requests_one_actionable_clarification() -> None:
    decision = capability_router.route_deterministic_request(
        "最近30天账号表现怎么样？",
        platform="douyin",
        registry=production_skill_registry,
        has_account=False,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.CLARIFY
    assert decision.skill_code == "account_data_analysis"
    assert decision.missing_field == "account_id"
    assert decision.clarifying_question == "请先选择需要操作的账号。"


def test_unsupported_industry_benchmark_requests_data_clarification() -> None:
    decision = capability_router.route_deterministic_request(
        "分析行业平均播放量",
        platform="douyin",
        registry=production_skill_registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.CLARIFY
    assert decision.skill_code == "account_data_analysis"
    assert decision.missing_field == "benchmark_data"


def test_analysis_constraint_does_not_trigger_a_long_term_strategy_route() -> None:
    decision = capability_router.route_deterministic_request(
        "分析最近30天播放量，但不要生成长期策略",
        platform="douyin",
        registry=production_skill_registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.SKILL
    assert decision.skill_code == "account_data_analysis"


@pytest.mark.parametrize(
    "message",
    [
        "不要给当前账号体检",
        "只查询",
        "只查询当前账号数据，再做一次体检",
        "帮我看看账号怎么样，顺便给个建议",
    ],
)
def test_deterministic_request_defers_negated_or_ambiguous_requests(
    registry: SkillRegistry, message: str
) -> None:
    """Catches broad keyword matching that could trigger an external Skill."""

    assert (
        capability_router.route_deterministic_request(
            message,
            platform="douyin",
            registry=registry,
            has_account=True,
        )
        is None
    )


def test_deterministic_request_defers_negated_only_data_query(
    registry: SkillRegistry,
) -> None:
    """Catches a negated only-query request being routed as a query."""

    assert (
        capability_router.route_deterministic_request(
            "不要只查询当前账号数据",
            platform="douyin",
            registry=registry,
            has_account=True,
        )
        is None
    )


def test_deterministic_request_answers_data_query_capability_question(
    registry: SkillRegistry,
) -> None:
    decision = capability_router.route_deterministic_request(
        "你能查询当前账号数据吗？",
        platform="douyin",
        registry=registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.ANSWER
    assert decision.reason == "deterministic_capability_question"


@pytest.mark.parametrize(
    ("message", "expected_skill"),
    [
        ("给我规划下周内容方向", "topic_planning"),
        ("生成一个视频脚本", "script_generation"),
        ("做一份表现复盘", "performance_review"),
    ],
)
def test_public_skill_aliases_route_without_model_classification(
    message: str,
    expected_skill: str,
) -> None:
    decision = capability_router.route_deterministic_request(
        message,
        platform="douyin",
        registry=production_skill_registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.SKILL
    assert decision.skill_code == expected_skill


def test_deterministic_request_defers_account_inspection_question(
    registry: SkillRegistry,
) -> None:
    """Catches a question about account inspection being mistaken for a Skill command."""

    assert (
        capability_router.route_deterministic_request(
            "为什么要给当前账号体检？",
            platform="douyin",
            registry=registry,
            has_account=True,
        )
        is None
    )


@pytest.mark.parametrize(
    ("message", "expected_skill"),
    [
        ("帮我重新做账号定位", "account_positioning"),
        ("给我做7天5个选题", "topic_planning"),
        ("写一个30秒口播脚本", "script_generation"),
        ("做发布准备检查", "publishing_preparation"),
        ("复盘最近30天的数据", "performance_review"),
        ("分析一下最近的评论互动复盘", "engagement_review"),
    ],
)
def test_migrated_operation_intents_route_to_typed_skills(
    message: str,
    expected_skill: str,
) -> None:
    decision = capability_router.route_deterministic_request(
        message,
        platform="douyin",
        registry=production_skill_registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.SKILL
    assert decision.skill_code == expected_skill


def test_weekly_content_production_routes_to_fresh_operation_iteration() -> None:
    decision = capability_router.route_deterministic_request(
        "结合最近数据和对标内容，规划并制作下周抖音内容",
        platform="douyin",
        registry=production_skill_registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.SKILL
    assert decision.skill_code == "operation_iteration"
    assert decision.requires_operation_task is True


@pytest.mark.parametrize(
    ("message", "expected_skill", "missing_field"),
    [
        ("现在直接发布", "content_publishing", "approved_publish_artifact_id"),
        ("根据脚本做视觉方案", "visual_brief_generation", "source_artifact_ids"),
        ("把这些内容排期", "content_calendar_planning", "source_artifact_ids"),
        ("安排下一周期运营", "operation_iteration", "confirmed_review_artifact_id"),
    ],
)
def test_migrated_operations_with_missing_artifact_request_clarification(
    message: str,
    expected_skill: str,
    missing_field: str,
) -> None:
    decision = capability_router.route_deterministic_request(
        message,
        platform="douyin",
        registry=production_skill_registry,
        has_account=True,
    )

    assert decision is not None
    assert decision.mode is TurnExecutionMode.CLARIFY
    assert decision.skill_code == expected_skill
    assert decision.missing_field == missing_field


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


def test_query_route_rejects_operation_task_without_clarification_fields() -> None:
    with pytest.raises(ValidationError, match="QUERY routes cannot require an operation task"):
        TurnRouteDecision(
            mode=TurnExecutionMode.QUERY,
            intent="account_data_query",
            confidence=1,
            reason="test",
            requires_operation_task=True,
        )
