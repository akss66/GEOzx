"""交付物 payload 的多态 schema 注册表。

`Deliverable.payload`（JSONB）的结构因 `type` 而异。入库前按 type 取对应 Pydantic
schema 校验，确保结构正确。这里建立注册表骨架 + 注册 1~2 个示例类型；
其余 8 类交付物在 M1 各 Agent 落地时补全。
"""

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DeliverableType
from app.schemas.artifacts import ScriptPresentationFormat


class DeliverablePayload(BaseModel):
    """所有交付物 payload 的基类。禁止未知字段，确保结构严格。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


_REGISTRY: dict[DeliverableType, type[DeliverablePayload]] = {}


def register(dtype: DeliverableType):
    """装饰器：把某 payload schema 注册到对应交付物类型。"""

    def _decorator(cls: type[DeliverablePayload]) -> type[DeliverablePayload]:
        _REGISTRY[dtype] = cls
        return cls

    return _decorator


def get_schema(dtype: DeliverableType) -> type[DeliverablePayload] | None:
    """取某类型的 payload schema；未注册返回 None。"""
    return _REGISTRY.get(dtype)


def registered_types() -> list[DeliverableType]:
    return list(_REGISTRY.keys())


def validate_payload(dtype: DeliverableType, data: dict) -> DeliverablePayload:
    """按交付物类型校验 payload；未注册类型抛 KeyError，结构不符抛 ValidationError。"""
    schema = _REGISTRY.get(dtype)
    if schema is None:
        raise KeyError(f"未注册的交付物类型: {dtype.value}")
    return schema.model_validate(data)


# —— 示例 schema（M1 补全其余）——


@register(DeliverableType.POSITIONING_STRATEGY)
class PositioningStrategyPayload(DeliverablePayload):
    """01 账号定位专家：定位策略文档。"""

    account_persona: str = Field(min_length=1)
    target_audience: str = Field(min_length=1)
    differentiation: list[str] = Field(min_length=2)
    content_pillars: list[str] = Field(min_length=2)


@register(DeliverableType.VIDEO_SCRIPT)
class VideoScriptPayload(DeliverablePayload):
    """02 编导文案专家：视频脚本。"""

    title: str = Field(min_length=1)
    hook: str = Field(min_length=1)
    scenes: list[str] = Field(min_length=3)
    duration_seconds: int = Field(gt=0)
    presentation_format: ScriptPresentationFormat = "storyboard"
    bgm_suggestion: str | None = None


@register(DeliverableType.ART_PROMPT)
class ArtPromptPayload(DeliverablePayload):
    """03 美术指导专家：视觉风格 + 结构化 AI 提示词。"""

    visual_style: str = Field(min_length=1)
    prompts: list[str] = Field(min_length=2)
    negative_prompt: str | None = None
    aspect_ratio: str = Field(default="9:16", min_length=1)


@register(DeliverableType.VIDEO_ASSET)
class VideoAssetPayload(DeliverablePayload):
    """04 视频创作专家：生成参数计划 + 真实出片结果（Ark Seedance，E7）。"""

    tool: str = Field(min_length=1)
    clips: list[dict] = Field(min_length=1)
    resolution: str = Field(min_length=1)
    notes: str | None = None
    # E7 真实生成结果（Ark 异步出片）
    video_url: str | None = None
    gen_task_id: str | None = None
    gen_status: str | None = None


@register(DeliverableType.EDITED_VIDEO)
class EditedVideoPayload(DeliverablePayload):
    """05 剪辑专家：剪辑说明 + 成片交付清单。"""

    cut_plan: list[str] = Field(min_length=1)
    captions: list[str] = Field(min_length=1)
    transitions: str = Field(min_length=1)
    deliverables: list[str] = Field(min_length=1)
    platform_variants: list[str] = Field(min_length=1)


@register(DeliverableType.REVIEW_REPORT)
class ReviewReportPayload(DeliverablePayload):
    """06 账号运营专家：复盘报告 + 优化建议。"""

    period: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    key_metrics: dict = Field(min_length=1)
    highlights: list[str] = Field(min_length=1)
    issues: list[str] = Field(min_length=1)
    optimization_suggestions: list[str] = Field(min_length=1)


@register(DeliverableType.TOPIC_PLAN)
class TopicPlanPayload(DeliverablePayload):
    theme: str
    topics: list[dict]
    posting_notes: list[str] = Field(default_factory=list)


@register(DeliverableType.PUBLISH_CALENDAR)
class PublishCalendarPayload(DeliverablePayload):
    period: str
    items: list[dict]
    operating_notes: list[str] = Field(default_factory=list)


@register(DeliverableType.AD_PLAN)
class AdPlanPayload(DeliverablePayload):
    objective: str = Field(min_length=1)
    target_audience: str = Field(min_length=1)
    budget_strategy: str = Field(min_length=1)
    creative_directions: list[str] = Field(min_length=2)
    risk_controls: list[str] = Field(min_length=2)
    measurement: dict = Field(min_length=1)


@register(DeliverableType.CS_RECORD)
class CustomerServiceRecordPayload(DeliverablePayload):
    period: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    common_questions: list[str] = Field(min_length=1)
    sentiment: dict = Field(min_length=1)
    response_guidelines: list[str] = Field(min_length=1)
    content_opportunities: list[str] = Field(min_length=1)
