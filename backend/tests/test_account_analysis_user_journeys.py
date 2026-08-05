"""User-facing routing acceptance journeys for evidence-driven account analysis."""

import pytest

from app.orchestrator.capability_router import route_deterministic_request
from app.orchestrator.skills.registry import skill_registry


@pytest.mark.parametrize(
    ("message", "expected_mode", "expected_skill"),
    [
        ("我现在账号有数据吗？", "query", "account_data_query"),
        ("最近30天账号表现怎么样？", "skill", "account_data_analysis"),
        ("播放量从什么时候开始下降？", "skill", "account_data_analysis"),
        ("哪个指标变化最大？", "skill", "account_data_analysis"),
        ("表现最差的5条作品是什么？", "skill", "account_data_analysis"),
        ("点赞下降但分享上涨说明什么？", "skill", "account_data_analysis"),
        ("目前的数据够不够判断留存问题？", "skill", "account_data_analysis"),
        ("只分析现状，不生成30天策略。", "skill", "account_data_analysis"),
    ],
)
def test_account_analysis_user_journeys(
    message: str,
    expected_mode: str,
    expected_skill: str | None,
) -> None:
    route = route_deterministic_request(
        message,
        platform="douyin",
        registry=skill_registry,
        has_account=True,
    )

    assert route is not None
    assert route.mode.value == expected_mode
    assert route.skill_code == expected_skill


def test_account_analysis_without_selected_account_stops_before_execution() -> None:
    route = route_deterministic_request(
        "最近30天账号表现怎么样？",
        platform="douyin",
        registry=skill_registry,
        has_account=False,
    )

    assert route is not None
    assert route.mode.value == "clarify"
    assert route.skill_code == "account_data_analysis"
    assert route.clarifying_question is not None
    assert "账号" in route.clarifying_question
