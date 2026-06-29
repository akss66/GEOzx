"""流水线定义：有序的「Agent 步 / 质量门步」序列。

M1 起逐个把 DummyAgent 替换为真实 LLMAgent：
- E2-01 定位 → PositioningAgent（真实 DeepSeek）✅
- E2-02~06 编导/美术/视频/剪辑/运营 → 后续切片替换。
"""

from dataclasses import dataclass

from app.agents.base import BaseAgent
from app.agents.dummy import DummyAgent
from app.agents.positioning import PositioningAgent
from app.models.enums import ContentStage, DeliverableType, GateType


@dataclass
class AgentStep:
    stage: ContentStage
    agent: BaseAgent


@dataclass
class GateStep:
    gate: GateType


# —— Dummy 占位 payload（满足对应 schema）——

_SCRIPT_PAYLOAD = {
    "title": "新品开箱：三分钟看懂值不值",
    "hook": "这台机器，贵的有道理吗？",
    "scenes": ["开箱", "上手实测", "结论"],
    "duration_seconds": 45,
    "bgm_suggestion": "轻快电子",
}


PIPELINE: list[AgentStep | GateStep] = [
    AgentStep(ContentStage.POSITIONING, PositioningAgent()),  # 真实 DeepSeek
    GateStep(GateType.POSITIONING_REVIEW),  # 自动通过
    AgentStep(
        ContentStage.CONTENT_DIRECTION,
        DummyAgent("02-content", DeliverableType.VIDEO_SCRIPT, _SCRIPT_PAYLOAD),
    ),
    GateStep(GateType.SCRIPT_COMPLIANCE),  # 强制人工
]
