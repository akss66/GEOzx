"""Build deterministic, typed inputs for one main-Agent capability request."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.schemas.attachment import AttachmentContext
from app.schemas.capability_request import CapabilityRequest

_DAY_PATTERN = re.compile(r"(?P<value>\d{1,3})\s*天")
_TOPIC_COUNT_PATTERN = re.compile(r"(?P<value>\d{1,2})\s*个?\s*选题")
_DURATION_PATTERNS = (
    re.compile(r"(?P<value>\d{1,4})\s*秒.{0,8}(?:脚本|视频)"),
    re.compile(r"(?:脚本|视频).{0,8}(?P<value>\d{1,4})\s*秒"),
)
_NEGATED_TOPIC_PATTERN = re.compile(r"(?:不要|不需要|无需|别).{0,8}选题")


def extract_structured_constraints(message: str) -> dict[str, Any]:
    """Extract only explicit, low-ambiguity operating constraints from one message."""

    normalized = "".join(message.strip().split())
    if not normalized:
        return {}

    extracted: dict[str, Any] = {}
    if "只看数据" in normalized or "只查数据" in normalized:
        extracted["requested_output"] = "data"

    if "只诊断" in normalized:
        extracted["requested_output"] = "diagnosis"
    if "不生成策略" in normalized or "不要生成策略" in normalized:
        extracted["generate_strategy"] = False

    if "选题" in normalized and not _NEGATED_TOPIC_PATTERN.search(normalized):
        day_match = _DAY_PATTERN.search(normalized)
        topic_match = _TOPIC_COUNT_PATTERN.search(normalized)
        if day_match is not None:
            extracted["days"] = int(day_match.group("value"))
        if topic_match is not None:
            extracted["topic_count"] = int(topic_match.group("value"))

    if "脚本" in normalized or "视频" in normalized:
        for pattern in _DURATION_PATTERNS:
            duration_match = pattern.search(normalized)
            if duration_match is not None:
                extracted["duration_seconds"] = int(duration_match.group("value"))
                break

    return extracted


def build_capability_request(
    *,
    user: Any,
    thread: Any,
    turn: Any,
    run: Any,
    request_payload: Mapping[str, Any],
    attachment_contexts: list[AttachmentContext] | None = None,
) -> CapabilityRequest:
    """Create one immutable request while preserving explicit-field precedence."""

    message = str(turn.user_input).strip()
    structured_input = extract_structured_constraints(message)
    explicit_input = request_payload.get("structured_input")
    if isinstance(explicit_input, Mapping):
        structured_input.update({str(key): value for key, value in explicit_input.items()})

    attachment_ids = _deduplicate_positive_ids(request_payload.get("attachment_ids"))
    requested_skill_code = request_payload.get("requested_skill_code")
    constraints: list[str] = []
    if structured_input.get("generate_strategy") is False:
        constraints.append("do_not_generate_strategy")

    return CapabilityRequest(
        org_id=user.org_id,
        user_id=user.id,
        account_id=thread.account_id,
        thread_id=thread.id,
        turn_id=turn.id,
        run_id=run.id,
        message=message,
        requested_skill_code=(
            str(requested_skill_code).strip() if requested_skill_code is not None else None
        ),
        execution_preference=str(request_payload.get("execution_preference") or "AUTO"),
        structured_input=structured_input,
        constraints=constraints,
        attachment_ids=attachment_ids,
        attachment_contexts=attachment_contexts or [],
    )


def _deduplicate_positive_ids(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0 or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


__all__ = ["build_capability_request", "extract_structured_constraints"]
