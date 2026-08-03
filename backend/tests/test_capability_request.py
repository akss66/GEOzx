from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.capability_request import (
    build_capability_request,
    extract_structured_constraints,
)


def test_extracts_typed_operating_constraints() -> None:
    assert extract_structured_constraints("规划未来14天的10个选题") == {
        "days": 14,
        "topic_count": 10,
    }
    assert extract_structured_constraints("生成一个30秒脚本") == {
        "duration_seconds": 30,
    }
    assert extract_structured_constraints("只诊断，不生成策略") == {
        "generate_strategy": False,
        "requested_output": "diagnosis",
    }


def test_ignores_ambiguous_or_unrelated_numbers() -> None:
    assert extract_structured_constraints("分析一下最近的数据") == {}
    assert extract_structured_constraints("播放量是14，帮我看看") == {}
    assert extract_structured_constraints("不要做10个选题，只看数据") == {
        "requested_output": "data",
    }


def test_builds_frozen_account_scoped_capability_request() -> None:
    request = build_capability_request(
        user=SimpleNamespace(id=7, org_id=3),
        thread=SimpleNamespace(id=11, account_id=13),
        turn=SimpleNamespace(id=17, user_input="规划未来14天的10个选题"),
        run=SimpleNamespace(id=19),
        request_payload={
            "requested_skill_code": "topic_planning",
            "execution_preference": "FORMAL_TASK",
            "attachment_ids": [23, 23, 29],
        },
    )

    assert request.model_dump(mode="json") == {
        "org_id": 3,
        "user_id": 7,
        "account_id": 13,
        "thread_id": 11,
        "turn_id": 17,
        "run_id": 19,
        "message": "规划未来14天的10个选题",
        "requested_skill_code": "topic_planning",
        "execution_preference": "FORMAL_TASK",
        "structured_input": {"days": 14, "topic_count": 10},
        "constraints": [],
        "attachment_ids": [23, 29],
    }

    with pytest.raises(ValidationError):
        request.account_id = 99


def test_explicit_structured_input_wins_over_message_extraction() -> None:
    request = build_capability_request(
        user=SimpleNamespace(id=7, org_id=3),
        thread=SimpleNamespace(id=11, account_id=13),
        turn=SimpleNamespace(id=17, user_input="规划未来14天的10个选题"),
        run=SimpleNamespace(id=19),
        request_payload={
            "execution_preference": "AUTO",
            "structured_input": {"days": 7},
        },
    )

    assert request.structured_input == {"days": 7, "topic_count": 10}
