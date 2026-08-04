from app.orchestrator.operation_quality import (
    evaluate_script_quality,
    normalize_script_text,
)
from app.orchestrator.skills.operating_tasks import FilmingScript


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

