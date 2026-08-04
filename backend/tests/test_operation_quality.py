from copy import deepcopy
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.models.enums import AgentCode
from app.orchestrator.operation_quality import (
    ArtifactQuality,
    QualityCheck,
    evaluate_script_quality,
    normalize_script_text,
)
from app.orchestrator.skill_runtime import _build_operating_report
from app.orchestrator.skills.content_calendar_planning import CalendarSlot
from app.orchestrator.skills.operating_tasks import (
    FilmingScript,
    TopicPlanItem,
    WeeklyOperationPackage,
)
from app.orchestrator.skills.registry import skill_registry
from app.orchestrator.skills.visual_brief_generation import VisualProductionItem


def _script(index: int, *, hook: str, voiceover: str) -> FilmingScript:
    return FilmingScript(
        script_id=f"script-{index:02d}",
        topic_id=f"topic-{index:02d}",
        title=f"拍摄稿 {index}",
        hook=hook,
        voiceover=voiceover,
        shot_list=[f"镜头 {index}-1", f"镜头 {index}-2", f"镜头 {index}-3"],
        duration_seconds=60,
        cta=f"评论区回复 {index}",
        constraints_hit=[],
    )


def test_script_normalization_handles_chinese_english_width_and_punctuation() -> None:
    assert normalize_script_text(" 价格！ Ｐｒｉｃｅ, A\n") == "价格pricea"


def test_five_copied_scripts_fail_deterministic_duplicate_gate() -> None:
    copied = "这是一段足够长的中文口播正文，用于验证脚本复制检测不会因为标点变化而漏判。"
    scripts = [
        _script(index, hook="开头钩子！", voiceover=f"{copied} 第一步。第二步，第三步。")
        for index in range(1, 6)
    ]
    # Make content identical after normalization while retaining different IDs/titles.
    for item in scripts:
        item.voiceover = f"{copied} 第一步。第二步，第三步。"

    quality = evaluate_script_quality(
        scripts,
        expected_topic_ids=[f"topic-{index:02d}" for index in range(1, 6)],
        required_constraints={},
    )

    assert quality.status == "needs_review"
    duplicate = next(check for check in quality.checks if check.code == "script_distinctness")
    assert duplicate.passed is False
    assert duplicate.item_ids == [
        "script-01",
        "script-02",
        "script-03",
        "script-04",
        "script-05",
    ]


def test_short_distinct_scripts_do_not_trigger_similarity_false_positive() -> None:
    scripts = [
        _script(index, hook=f"钩子{index}", voiceover=f"短稿{index}") for index in range(1, 6)
    ]

    quality = evaluate_script_quality(
        scripts,
        expected_topic_ids=[f"topic-{index:02d}" for index in range(1, 6)],
        required_constraints={},
    )

    assert quality.status == "passed"
    assert quality.score == 100


def test_weekly_package_self_validates_nested_items_and_operating_invariants() -> None:
    quality = ArtifactQuality(
        status="passed",
        score=100,
        checks=[QualityCheck(code="complete", passed=True, message="完整")],
    )
    scripts = [
        _script(index, hook=f"钩子 {index}", voiceover=f"完整口播 {index}") for index in range(1, 6)
    ]
    start = date(2026, 8, 10)
    zone = ZoneInfo("Asia/Shanghai")
    slots = [
        CalendarSlot(
            slot_id=f"slot-{index:02d}",
            date=start + timedelta(days=index - 1),
            slot_type="publish" if index <= 5 else "review_buffer",
            title=f"第 {index} 天",
            owner="运营",
            readiness="ready" if index <= 5 else "buffer",
            topic_id=f"topic-{index:02d}" if index <= 5 else None,
            script_id=f"script-{index:02d}" if index <= 5 else None,
            scheduled_at=(datetime(2026, 8, 9 + index, 10, tzinfo=zone) if index <= 5 else None),
        )
        for index in range(1, 8)
    ]

    payload = {
        "source_artifacts": [
            {
                "artifact_id": index,
                "artifact_type": "topic_plan",
                "version": 1,
            }
            for index in range(1, 5)
        ],
        "evidence_refs": [{"kind": "data_import_batch", "id": 1}],
        "topics": [
            TopicPlanItem(
                topic_id=f"topic-{index:02d}",
                title=f"选题 {index}",
                angle="实测",
                format="short_video",
            ).model_dump(mode="json")
            for index in range(1, 6)
        ],
        "scripts": [item.model_dump(mode="json") for item in scripts],
        "visuals": [
            VisualProductionItem(
                visual_id=f"visual-{index:02d}",
                script_id=f"script-{index:02d}",
                topic_id=f"topic-{index:02d}",
                cover_copy=f"封面 {index}",
                composition="主体居中",
                shot_list=["开场", "实测", "结尾"],
                asset_checklist=["产品素材"],
                platform_constraints=["竖屏 9:16"],
            )
            for index in range(1, 6)
        ],
        "calendar_slots": slots,
        "quality": {
            "topics": quality,
            "scripts": quality,
            "visuals": quality,
            "calendar": quality,
        },
        "participating_experts": ["02-content-director"],
        "manual_publish_checklist": ["人工确认标题"],
        "next_steps": [
            {"code": "start_filming", "label": "按 5 条拍摄稿开始拍摄"},
            {"code": "confirm_manual_schedule", "label": "确认 7 天安排"},
        ],
    }
    assert WeeklyOperationPackage.model_validate(payload).calendar_slots[0].slot_id == "slot-01"

    malformed_visuals = deepcopy(payload)
    malformed_visuals["visuals"] = [{"visual_id": f"visual-{index:02d}"} for index in range(1, 6)]
    with pytest.raises(ValidationError):
        WeeklyOperationPackage.model_validate(malformed_visuals)

    malformed_topics_and_scripts = deepcopy(payload)
    malformed_topics_and_scripts["topics"][0]["title"] = ""
    malformed_topics_and_scripts["scripts"][0]["voiceover"] = ""
    with pytest.raises(ValidationError):
        WeeklyOperationPackage.model_validate(malformed_topics_and_scripts)

    duplicate_slot = deepcopy(payload)
    duplicate_slot["calendar_slots"][1] = duplicate_slot["calendar_slots"][0]
    with pytest.raises(ValidationError):
        WeeklyOperationPackage.model_validate(duplicate_slot)

    wrong_readiness = deepcopy(payload)
    wrong_readiness["calendar_slots"][5].readiness = "ready"
    with pytest.raises(ValidationError):
        WeeklyOperationPackage.model_validate(wrong_readiness)

    missing_next_step = deepcopy(payload)
    missing_next_step["next_steps"] = missing_next_step["next_steps"][:1]
    with pytest.raises(ValidationError):
        WeeklyOperationPackage.model_validate(missing_next_step)

    empty_human_guidance = deepcopy(payload)
    empty_human_guidance["participating_experts"] = [""]
    empty_human_guidance["manual_publish_checklist"] = [""]
    with pytest.raises(ValidationError):
        WeeklyOperationPackage.model_validate(empty_human_guidance)


@pytest.mark.parametrize(
    ("skill_code", "frozen_input", "agent_code"),
    [
        (
            "script_generation",
            {"duration_seconds": 60, "presentation_format": "storyboard"},
            AgentCode.CONTENT_DIRECTOR,
        ),
        (
            "visual_brief_generation",
            {"source_artifact_ids": [1]},
            AgentCode.ART_DIRECTOR,
        ),
    ],
)
def test_empty_standalone_specialist_output_never_gets_passing_quality(
    skill_code,
    frozen_input,
    agent_code,
) -> None:
    report, _deliverable_type, _payload = _build_operating_report(
        definition=skill_registry.get(skill_code),
        account_id=1,
        platform="douyin",
        user_input="生成内容",
        frozen_input=frozen_input,
        tool_results={},
        expert_results=[
            SimpleNamespace(
                output={},
                invocation=SimpleNamespace(agent_code=agent_code),
            )
        ],
        evidence_refs=[{"artifact_id": 1, "artifact_type": "video_script", "version": 1}],
        source_artifacts=[],
        operation_mode=False,
        execution_date=date(2026, 8, 10),
    )

    assert report["quality"]["status"] == "needs_review"
    required = next(
        item for item in report["quality"]["checks"] if item["code"].endswith("required_fields")
    )
    assert required["passed"] is False
