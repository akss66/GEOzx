"""流水线定义：有序的「Agent 步 / 质量门步」序列。

M1 起逐个把 DummyAgent 替换为真实 LLMAgent：
- E2-01 定位 → PositioningAgent（真实 DeepSeek）✅
- E2-02 编导 → ContentAgent（读上游定位，真实 DeepSeek）✅
- E2-03~06 美术/视频/剪辑/运营 → 后续切片替换。
"""

from dataclasses import dataclass

from app.agents.base import BaseAgent
from app.agents.content import ContentAgent
from app.agents.positioning import PositioningAgent
from app.models.enums import ContentStage, GateType


@dataclass
class AgentStep:
    stage: ContentStage
    agent: BaseAgent


@dataclass
class GateStep:
    gate: GateType


PIPELINE: list[AgentStep | GateStep] = [
    AgentStep(ContentStage.POSITIONING, PositioningAgent()),  # 真实 DeepSeek
    GateStep(GateType.POSITIONING_REVIEW),  # 自动通过
    AgentStep(ContentStage.CONTENT_DIRECTION, ContentAgent()),  # 真实 DeepSeek，读上游定位
    GateStep(GateType.SCRIPT_COMPLIANCE),  # 强制人工
]
