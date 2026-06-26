"""交付物 schema 注册表测试（纯单元，无需 DB）。"""

import pytest
from pydantic import ValidationError

from app.models.enums import DeliverableType
from app.schemas.deliverable import (
    PositioningStrategyPayload,
    get_schema,
    validate_payload,
)


def test_registry_dispatch() -> None:
    assert get_schema(DeliverableType.POSITIONING_STRATEGY) is PositioningStrategyPayload


def test_validate_ok() -> None:
    payload = validate_payload(
        DeliverableType.POSITIONING_STRATEGY,
        {
            "account_persona": "硬核数码测评",
            "target_audience": "25-35 男性科技爱好者",
            "differentiation": ["真机长测", "拆解"],
            "content_pillars": ["新品首发", "横评"],
        },
    )
    assert isinstance(payload, PositioningStrategyPayload)
    assert payload.account_persona == "硬核数码测评"


def test_validate_missing_field_raises() -> None:
    with pytest.raises(ValidationError):
        validate_payload(
            DeliverableType.POSITIONING_STRATEGY,
            {"account_persona": "x"},  # 缺少必填字段
        )


def test_validate_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        validate_payload(
            DeliverableType.VIDEO_SCRIPT,
            {
                "title": "t",
                "hook": "h",
                "scenes": ["s1"],
                "duration_seconds": 30,
                "unexpected": "nope",  # extra=forbid
            },
        )


def test_unregistered_type_raises() -> None:
    with pytest.raises(KeyError):
        validate_payload(DeliverableType.AD_PLAN, {})
