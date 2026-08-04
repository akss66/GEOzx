"""Deterministic quality checks for weekly operation artifacts."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_SIMILARITY_THRESHOLD = 0.92
_MIN_SIMILARITY_LENGTH = 40


class QualityCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    passed: bool
    message: str = Field(min_length=1)
    item_ids: list[str] = Field(default_factory=list)


class ArtifactQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["operation-quality/v1"] = "operation-quality/v1"
    status: Literal["passed", "needs_review"]
    score: int = Field(ge=0, le=100)
    threshold: int = Field(default=80, ge=0, le=100)
    checks: list[QualityCheck] = Field(min_length=1)


def normalize_script_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(char for char in normalized if char.isalnum())


def evaluate_script_quality(
    scripts: Sequence[Any],
    *,
    expected_topic_ids: Sequence[str],
    required_constraints: dict[str, list[str]],
) -> ArtifactQuality:
    script_ids = [str(item.script_id) for item in scripts]
    topic_ids = [str(item.topic_id) for item in scripts]
    checks = [
        QualityCheck(
            code="script_count",
            passed=len(scripts) == len(expected_topic_ids),
            message=f"应生成 {len(expected_topic_ids)} 条拍摄稿。",
            item_ids=script_ids,
        ),
        QualityCheck(
            code="topic_mapping",
            passed=(
                len(set(topic_ids)) == len(topic_ids)
                and set(topic_ids) == set(expected_topic_ids)
            ),
            message="拍摄稿必须与选题一一对应。",
            item_ids=script_ids,
        ),
        QualityCheck(
            code="required_fields",
            passed=all(
                all(
                    (
                        str(getattr(item, field)).strip()
                        if field != "shot_list"
                        else bool(getattr(item, field))
                    )
                    for field in ("title", "hook", "voiceover", "shot_list", "cta")
                )
                for item in scripts
            ),
            message="每条拍摄稿必须包含标题、钩子、正文、镜头和 CTA。",
            item_ids=script_ids,
        ),
    ]
    duplicate_ids: set[str] = set()
    normalized = [normalize_script_text(f"{item.hook}{item.voiceover}") for item in scripts]
    for left_index, left in enumerate(normalized):
        for right_index in range(left_index + 1, len(normalized)):
            right = normalized[right_index]
            is_duplicate = left == right or (
                len(left) >= _MIN_SIMILARITY_LENGTH
                and len(right) >= _MIN_SIMILARITY_LENGTH
                and SequenceMatcher(None, left, right).ratio() >= _SIMILARITY_THRESHOLD
            )
            if is_duplicate:
                duplicate_ids.update((script_ids[left_index], script_ids[right_index]))
    checks.append(
        QualityCheck(
            code="script_distinctness",
            passed=not duplicate_ids,
            message="5 条拍摄稿不得是同一内容的复制或高相似改写。",
            item_ids=sorted(duplicate_ids),
        )
    )
    constraint_failures: list[str] = []
    by_topic = {str(item.topic_id): item for item in scripts}
    for topic_id, requirements in required_constraints.items():
        item = by_topic.get(topic_id)
        hits = set(item.constraints_hit if item is not None else [])
        if item is None or not set(requirements).issubset(hits):
            constraint_failures.append(str(item.script_id) if item is not None else topic_id)
    checks.append(
        QualityCheck(
            code="constraint_coverage",
            passed=not constraint_failures,
            message="目标拍摄稿必须明确记录已命中的补充要求。",
            item_ids=constraint_failures,
        )
    )
    return _quality(checks)


def evaluate_topic_quality(
    topics: Sequence[Any],
    *,
    expected_count: int,
) -> ArtifactQuality:
    topic_ids = [str(item.topic_id) for item in topics]
    signatures = [
        normalize_script_text(f"{item.title}{item.angle}{item.format}") for item in topics
    ]
    duplicate_ids = {
        topic_ids[index]
        for index, signature in enumerate(signatures)
        if signature and signatures.count(signature) > 1
    }
    return _quality(
        [
            QualityCheck(
                code="topic_count",
                passed=len(topics) == expected_count,
                message=f"应生成 {expected_count} 个选题。",
                item_ids=topic_ids,
            ),
            QualityCheck(
                code="topic_id_uniqueness",
                passed=len(topic_ids) == len(set(topic_ids)),
                message="选题 ID 必须唯一。",
                item_ids=topic_ids,
            ),
            QualityCheck(
                code="topic_required_fields",
                passed=all(
                    str(getattr(item, field)).strip()
                    for item in topics
                    for field in ("title", "angle", "format")
                ),
                message="每个选题必须包含标题、角度和形式。",
                item_ids=topic_ids,
            ),
            QualityCheck(
                code="topic_distinctness",
                passed=not duplicate_ids,
                message="选题内容不得重复。",
                item_ids=sorted(duplicate_ids),
            ),
        ]
    )


def evaluate_visual_quality(
    visuals: Sequence[Any],
    *,
    expected_script_ids: Sequence[str],
) -> ArtifactQuality:
    visual_ids = [str(item.visual_id) for item in visuals]
    script_ids = [str(item.script_id) for item in visuals]
    return _quality(
        [
            QualityCheck(
                code="visual_count",
                passed=len(visuals) == len(expected_script_ids),
                message=f"应生成 {len(expected_script_ids)} 组视觉制作要求。",
                item_ids=visual_ids,
            ),
            QualityCheck(
                code="visual_script_mapping",
                passed=(
                    len(script_ids) == len(set(script_ids))
                    and set(script_ids) == set(expected_script_ids)
                ),
                message="视觉要求必须与拍摄稿一一对应。",
                item_ids=visual_ids,
            ),
            QualityCheck(
                code="visual_required_fields",
                passed=all(
                    str(item.topic_id).strip()
                    and str(item.cover_copy).strip()
                    and str(item.composition).strip()
                    and bool(item.shot_list)
                    and bool(item.asset_checklist)
                    and bool(item.platform_constraints)
                    for item in visuals
                ),
                message="每组视觉要求必须包含选题绑定、封面、构图、镜头、素材和平台要求。",
                item_ids=visual_ids,
            ),
        ]
    )


def evaluate_calendar_quality(
    slots: Sequence[Any],
    *,
    expected_script_ids: Sequence[str],
) -> ArtifactQuality:
    slot_ids = [str(item.slot_id) for item in slots]
    publish_slots = [item for item in slots if item.slot_type == "publish"]
    buffer_slots = [item for item in slots if item.slot_type == "review_buffer"]
    publish_script_ids = [str(item.script_id) for item in publish_slots]
    dates = [item.date for item in slots]
    consecutive = len(dates) == 7 and all(
        (dates[index] - dates[index - 1]).days == 1 for index in range(1, len(dates))
    )
    return _quality(
        [
            QualityCheck(
                code="calendar_slot_count",
                passed=len(slots) == 7,
                message="7 天安排必须包含 7 个连续日期槽位。",
                item_ids=slot_ids,
            ),
            QualityCheck(
                code="calendar_consecutive_dates",
                passed=consecutive,
                message="排期日期必须连续且不可重复。",
                item_ids=slot_ids,
            ),
            QualityCheck(
                code="calendar_publish_mapping",
                passed=(
                    len(publish_slots) == len(expected_script_ids) == 5
                    and len(publish_script_ids) == len(set(publish_script_ids))
                    and set(publish_script_ids) == set(expected_script_ids)
                    and all(item.scheduled_at is not None for item in publish_slots)
                ),
                message="五个发布槽位必须各绑定一条拍摄稿并包含发布时间。",
                item_ids=[str(item.slot_id) for item in publish_slots],
            ),
            QualityCheck(
                code="calendar_buffer_slots",
                passed=(
                    len(buffer_slots) == 2
                    and all(item.script_id is None for item in buffer_slots)
                    and all(item.scheduled_at is None for item in buffer_slots)
                ),
                message="两个复盘缓冲槽位不得绑定拍摄稿或伪造发布时间。",
                item_ids=[str(item.slot_id) for item in buffer_slots],
            ),
        ]
    )


def _quality(checks: list[QualityCheck], *, threshold: int = 80) -> ArtifactQuality:
    score = round(100 * sum(item.passed for item in checks) / len(checks))
    return ArtifactQuality(
        status=(
            "passed"
            if all(item.passed for item in checks) and score >= threshold
            else "needs_review"
        ),
        score=score,
        threshold=threshold,
        checks=checks,
    )


__all__ = [
    "ArtifactQuality",
    "QualityCheck",
    "evaluate_calendar_quality",
    "evaluate_script_quality",
    "evaluate_topic_quality",
    "evaluate_visual_quality",
    "normalize_script_text",
]
