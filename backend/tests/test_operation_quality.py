from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.orchestrator.operation_quality import (
    ArtifactQuality,
    QualityCheck,
    evaluate_script_quality,
    normalize_script_text,
)
from app.orchestrator.skills.content_calendar_planning import CalendarSlot
from app.orchestrator.skills.operating_tasks import (
    FilmingScript,
    TopicPlanItem,
    WeeklyOperationPackage,
)


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
        _script(index, hook=f"钩子{index}", voiceover=f"短稿{index}")
        for index in range(1, 6)
    ]

    quality = evaluate_script_quality(
        scripts,
        expected_topic_ids=[f"topic-{index:02d}" for index in range(1, 6)],
        required_constraints={},
    )

    assert quality.status == "passed"
    assert quality.score == 100


def test_weekly_package_rejects_malformed_nested_visual_items() -> None:
    quality = ArtifactQuality(
        status="passed",
        score=100,
        checks=[QualityCheck(code="complete", passed=True, message="完整")],
    )
    scripts = [
        _script(index, hook=f"钩子 {index}", voiceover=f"完整口播 {index}")
        for index in range(1, 6)
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
            scheduled_at=(
                datetime(2026, 8, 9 + index, 10, tzinfo=zone) if index <= 5 else None
            ),
        )
        for index in range(1, 8)
    ]

    with pytest.raises(ValidationError):
        WeeklyOperationPackage.model_validate(
            {
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
                    )
                    for index in range(1, 6)
                ],
                "scripts": scripts,
                "visuals": [{"visual_id": f"visual-{index:02d}"} for index in range(1, 6)],
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
                    {"code": "start_filming", "label": "按 5 条拍摄稿开始拍摄"}
                ],
            }
        )
