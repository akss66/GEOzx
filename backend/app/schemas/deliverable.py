"""交付物 payload 的多态 schema 注册表。

`Deliverable.payload`（JSONB）的结构因 `type` 而异。入库前按 type 取对应 Pydantic
schema 校验，确保结构正确。这里建立注册表骨架 + 注册 1~2 个示例类型；
其余 8 类交付物在 M1 各 Agent 落地时补全。
"""

from pydantic import BaseModel, ConfigDict

from app.models.enums import DeliverableType


class DeliverablePayload(BaseModel):
    """所有交付物 payload 的基类。禁止未知字段，确保结构严格。"""

    model_config = ConfigDict(extra="forbid")


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

    account_persona: str
    target_audience: str
    differentiation: list[str]
    content_pillars: list[str]


@register(DeliverableType.VIDEO_SCRIPT)
class VideoScriptPayload(DeliverablePayload):
    """02 编导文案专家：视频脚本。"""

    title: str
    hook: str
    scenes: list[str]
    duration_seconds: int
    bgm_suggestion: str | None = None
