"""Phase-two acceptance matrix for the operator-facing Skill lifecycle."""

from dataclasses import dataclass

import pytest

from app.orchestrator.capability_router import route_deterministic_request
from app.orchestrator.skills.registry import skill_registry
from app.schemas.conversation import TurnExecutionMode


@dataclass(frozen=True)
class LifecycleScenario:
    name: str
    message: str
    skill_code: str
    mode: TurnExecutionMode
    missing_field: str | None
    experts: tuple[str, ...]
    tools: tuple[str, ...]
    critic: str
    approval: str
    artifact_type: str


SCENARIOS = (
    LifecycleScenario(
        "positioning",
        "帮我做账号定位",
        "account_positioning",
        TurnExecutionMode.SKILL,
        None,
        ("01-positioning",),
        ("account.profile", "account.data_context"),
        "required",
        "none",
        "account_positioning",
    ),
    LifecycleScenario(
        "topic planning",
        "规划下周选题",
        "topic_planning",
        TurnExecutionMode.SKILL,
        None,
        ("02-content-director",),
        ("account.profile", "account.data_context"),
        "none",
        "none",
        "topic_plan",
    ),
    LifecycleScenario(
        "script generation",
        "生成一条30秒口播脚本",
        "script_generation",
        TurnExecutionMode.SKILL,
        None,
        ("02-content-director",),
        ("account.profile",),
        "none",
        "none",
        "video_script",
    ),
    LifecycleScenario(
        "visual brief",
        "根据已确认脚本做视觉方案",
        "visual_brief_generation",
        TurnExecutionMode.CLARIFY,
        "source_artifact_ids",
        ("03-art-director", "04-video-creator"),
        ("account.profile",),
        "required",
        "none",
        "visual_brief",
    ),
    LifecycleScenario(
        "content calendar",
        "把已有选题做成内容排期",
        "content_calendar_planning",
        TurnExecutionMode.CLARIFY,
        "source_artifact_ids",
        ("06-operator",),
        ("account.profile",),
        "required",
        "none",
        "content_calendar",
    ),
    LifecycleScenario(
        "publishing preparation",
        "做发布准备和发布检查",
        "publishing_preparation",
        TurnExecutionMode.SKILL,
        None,
        ("06-operator",),
        ("publish_package_prepare",),
        "none",
        "before_finish",
        "publish_calendar",
    ),
    LifecycleScenario(
        "approval-gated publishing",
        "现在发布这条内容",
        "content_publishing",
        TurnExecutionMode.CLARIFY,
        "approved_publish_artifact_id",
        (),
        ("platform.content_publish",),
        "none",
        "before_tools",
        "platform_publish_receipt",
    ),
    LifecycleScenario(
        "engagement review",
        "分析最近评论并做互动复盘",
        "engagement_review",
        TurnExecutionMode.SKILL,
        None,
        ("08-customer-service",),
        ("account.engagement_context",),
        "required",
        "none",
        "engagement_review",
    ),
    LifecycleScenario(
        "performance review",
        "复盘最近30天的数据表现",
        "performance_review",
        TurnExecutionMode.SKILL,
        None,
        ("06-operator", "02-content-director"),
        ("account.data_context",),
        "none",
        "none",
        "review_report",
    ),
    LifecycleScenario(
        "next operation cycle",
        "根据复盘安排下一周期运营",
        "operation_iteration",
        TurnExecutionMode.CLARIFY,
        "confirmed_review_artifact_id",
        (),
        (),
        "none",
        "none",
        "operation_execution_plan",
    ),
)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda item: item.name)
def test_operation_lifecycle_routes_to_a_truthful_skill_contract(
    scenario: LifecycleScenario,
) -> None:
    route = route_deterministic_request(
        scenario.message,
        platform="douyin",
        registry=skill_registry,
        has_account=True,
    )

    assert route is not None
    assert route.mode is scenario.mode
    assert route.skill_code == scenario.skill_code
    assert route.missing_field == scenario.missing_field
    assert route.requires_operation_task is (scenario.mode is TurnExecutionMode.SKILL)

    definition = skill_registry.get(scenario.skill_code)
    assert definition.expert_codes == scenario.experts
    assert definition.tool_codes == scenario.tools
    assert definition.critic_policy == scenario.critic
    assert definition.approval_policy == scenario.approval
    assert definition.artifact_type == scenario.artifact_type


def test_real_publish_is_douyin_only_and_never_exposed_as_an_expert_conclusion() -> None:
    definition = skill_registry.get("content_publishing")

    assert definition.supported_platforms == frozenset({"douyin"})
    assert definition.risk_level == "high"
    assert definition.expert_codes == ()
    assert definition.tool_codes == ("platform.content_publish",)


def test_iteration_is_a_composite_plan_not_a_main_agent_professional_output() -> None:
    definition = skill_registry.get("operation_iteration")

    assert definition.expert_codes == ()
    assert definition.tool_codes == ()
    assert definition.artifact_type == "operation_execution_plan"
