"""交付物 schema 注册表测试（纯单元，无需 DB）。"""

import pytest
from pydantic import ValidationError

from app.models.enums import DeliverableType
from app.schemas.deliverable import (
    AdPlanPayload,
    PositioningStrategyPayload,
    PublishPackagePayload,
    WechatArticlePayload,
    WechatImagePlanPayload,
    WechatRenderedArticlePayload,
    get_schema,
    validate_payload,
)
from app.services.artifacts import _normalize_artifact_type, validate_complete_artifact_payload
from app.services.deliverable_action_registry import REVISION_DELIVERABLE_TYPES

_INVALID_BUSINESS_PAYLOADS = [
    (
        DeliverableType.POSITIONING_STRATEGY,
        {
            "account_persona": "定位",
            "target_audience": "人群",
            "differentiation": ["只有一条"],
            "content_pillars": ["支柱一", "支柱二"],
        },
    ),
    (
        DeliverableType.VIDEO_SCRIPT,
        {
            "title": "标题",
            "hook": "钩子",
            "scenes": ["镜头一", "镜头二"],
            "duration_seconds": 0,
        },
    ),
    (
        DeliverableType.ART_PROMPT,
        {
            "visual_style": "风格",
            "prompts": ["只有一条"],
            "aspect_ratio": "9:16",
        },
    ),
    (
        DeliverableType.VIDEO_ASSET,
        {"tool": "planned", "clips": [], "resolution": ""},
    ),
    (
        DeliverableType.EDITED_VIDEO,
        {
            "cut_plan": [],
            "captions": ["字幕"],
            "transitions": "硬切",
            "deliverables": ["成片"],
            "platform_variants": ["抖音版"],
        },
    ),
    (
        DeliverableType.REVIEW_REPORT,
        {
            "period": "近 7 天",
            "summary": "结论",
            "key_metrics": {},
            "highlights": [],
            "issues": ["问题"],
            "optimization_suggestions": ["建议"],
        },
    ),
    (
        DeliverableType.AD_PLAN,
        {
            "objective": "验证",
            "target_audience": "人群",
            "budget_strategy": "小额测试",
            "creative_directions": ["方向一", "方向二"],
            "risk_controls": ["只有一条"],
            "measurement": {"primary": "成本"},
        },
    ),
    (
        DeliverableType.CS_RECORD,
        {
            "period": "近 7 天",
            "summary": "结论",
            "common_questions": [],
            "sentiment": {"overall": "中性"},
            "response_guidelines": ["原则"],
            "content_opportunities": ["机会"],
        },
    ),
]


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


def test_publish_package_registered_and_serializes() -> None:
    payload = validate_payload(
        DeliverableType.PUBLISH_PACKAGE,
        {
            "account_id": 42,
            "platform": "douyin",
            "readiness": "ready",
            "period": "2026-08-10 至 2026-08-16",
            "items": [{"slot_id": "slot-01"}],
            "operating_notes": ["仅创建手动发布任务"],
            "package": {"scripts": [{"script_id": "script-01"}]},
            "participating_experts": ["账号运营专家"],
        },
    )

    assert isinstance(payload, PublishPackagePayload)
    assert payload.model_dump(mode="json")["package"]["scripts"][0]["script_id"] == ("script-01")


def test_wechat_article_deliverables_use_the_shared_validation_and_revision_pipeline() -> None:
    article_payload = {
        "document": {
            "title": "Summer insulation guide",
            "digest": "Practical advice for a cooler home.",
            "blocks": [
                {
                    "type": "paragraph",
                    "block_id": "intro",
                    "text": "Start with the windows.",
                }
            ],
        }
    }

    payload = validate_complete_artifact_payload(DeliverableType.WECHAT_ARTICLE, article_payload)

    validated_article = validate_payload(DeliverableType.WECHAT_ARTICLE, article_payload)
    assert isinstance(validated_article, WechatArticlePayload)
    assert payload["document"]["title"] == "Summer insulation guide"

    image_plan = validate_complete_artifact_payload(
        DeliverableType.WECHAT_IMAGE_PLAN,
        {
            "slots": [
                {
                    "stable_key": "hero-window",
                    "purpose": "Explain the first visual.",
                    "aspect_ratio": "3:2",
                    "visual_brief": "A bright, practical close-up.",
                }
            ]
        },
    )
    rendered = validate_complete_artifact_payload(
        DeliverableType.WECHAT_RENDERED_ARTICLE,
        {"article_deliverable_id": 1, "rendered_html": "<p>Rendered locally.</p>"},
    )
    assert isinstance(
        validate_payload(DeliverableType.WECHAT_IMAGE_PLAN, image_plan), WechatImagePlanPayload
    )
    assert isinstance(
        validate_payload(DeliverableType.WECHAT_RENDERED_ARTICLE, rendered),
        WechatRenderedArticlePayload,
    )
    assert {
        _normalize_artifact_type(DeliverableType.WECHAT_ARTICLE),
        _normalize_artifact_type(DeliverableType.WECHAT_IMAGE_PLAN),
        _normalize_artifact_type(DeliverableType.WECHAT_RENDERED_ARTICLE),
    } == {"wechat_article", "wechat_image_plan", "wechat_rendered_article"}
    assert {
        DeliverableType.WECHAT_ARTICLE,
        DeliverableType.WECHAT_IMAGE_PLAN,
        DeliverableType.WECHAT_RENDERED_ARTICLE,
    } <= REVISION_DELIVERABLE_TYPES


@pytest.mark.parametrize(("deliverable_type", "payload"), _INVALID_BUSINESS_PAYLOADS)
def test_expert_contracts_reject_business_invalid_payloads(deliverable_type, payload):
    with pytest.raises(ValidationError):
        validate_payload(deliverable_type, payload)
