"""交付物 schema 注册表测试（纯单元，无需 DB）。"""

import pytest
from pydantic import ValidationError

from app.models.enums import DeliverableType
from app.schemas.deliverable import (
    AdPlanPayload,
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


def test_ad_plan_registered_and_validates() -> None:
    payload = validate_payload(
        DeliverableType.AD_PLAN,
        {
            "objective": "验证冷启动内容方向",
            "target_audience": "关注数码产品的理性消费者",
            "budget_strategy": "先小额验证，再按有效素材递增",
            "creative_directions": ["真实体验", "同价位对比"],
            "risk_controls": ["人工确认预算", "发布前合规复核"],
            "measurement": {"primary": "有效互动成本"},
        },
    )

    assert isinstance(payload, AdPlanPayload)
    assert payload.measurement["primary"] == "有效互动成本"
