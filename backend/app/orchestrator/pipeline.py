"""流水线定义：有序的「Agent 步 / 质量门步」序列。

T7 用 DummyAgent 跑通骨架（定位→自动门→编导→强制门→完成）；
M1 用真实 6 个创作 Agent 与 6 道门替换/扩展。
"""

from dataclasses import dataclass

from app.agents.base import BaseAgent
from app.agents.dummy import DummyAgent
from app.models.enums import ContentStage, DeliverableType, GateType


@dataclass
class AgentStep:
    stage: ContentStage
    agent: BaseAgent


@dataclass
class GateStep:
    gate: GateType


# —— Dummy 占位 payload（满足对应 schema）——

_POSITIONING_PAYLOAD = {
    "account_persona": "硬核数码测评",
    "target_audience": "25-35 岁科技爱好者",
    "differentiation": ["真机长测", "深度拆解"],
    "content_pillars": ["新品首发", "横向对比"],
}

_SCRIPT_PAYLOAD = {
    "title": "新品开箱：三分钟看懂值不值",
    "hook": "这台机器，贵的有道理吗？",
    "scenes": ["开箱", "上手实测", "结论"],
    "duration_seconds": 45,
    "bgm_suggestion": "轻快电子",
}


PIPELINE: list[AgentStep | GateStep] = [
    AgentStep(
        ContentStage.POSITIONING,
        DummyAgent("01-positioning", DeliverableType.POSITIONING_STRATEGY, _POSITIONING_PAYLOAD),
    ),
    GateStep(GateType.POSITIONING_REVIEW),  # 自动通过
    AgentStep(
        ContentStage.CONTENT_DIRECTION,
        DummyAgent("02-content", DeliverableType.VIDEO_SCRIPT, _SCRIPT_PAYLOAD),
    ),
    GateStep(GateType.SCRIPT_COMPLIANCE),  # 强制人工
]
