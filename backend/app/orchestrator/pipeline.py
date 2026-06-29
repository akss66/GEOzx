"""流水线定义：有序的「Agent 步 / 质量门步」序列。

M1 E3：主链路六阶段全部接真实 LLMAgent + 5 道门（Gate6 大额投放属并行投流链路，M3）。
定位→Gate1→编导→Gate2→Gate3(强制)→美术→视频→剪辑→Gate4→运营→Gate5(强制)→完成。
"""

from dataclasses import dataclass

from app.agents.art import ArtAgent
from app.agents.base import BaseAgent
from app.agents.content import ContentAgent
from app.agents.editing import EditingAgent
from app.agents.operation import OperationAgent
from app.agents.positioning import PositioningAgent
from app.agents.video import VideoAgent
from app.models.enums import ContentStage, GateType


@dataclass
class AgentStep:
    stage: ContentStage
    agent: BaseAgent


@dataclass
class GateStep:
    gate: GateType


# 六阶段主链路（SPEC 5.3）+ 5 道门（SPEC 5.5；Gate6 大额投放在 M3 并行投流链路）。
PIPELINE: list[AgentStep | GateStep] = [
    AgentStep(ContentStage.POSITIONING, PositioningAgent()),
    GateStep(GateType.POSITIONING_REVIEW),  # Gate1 定位审核：自动
    AgentStep(ContentStage.CONTENT_DIRECTION, ContentAgent()),
    GateStep(GateType.TOPIC_REVIEW),  # Gate2 选题审核：自动
    GateStep(GateType.SCRIPT_COMPLIANCE),  # Gate3 脚本合规：强制人工
    AgentStep(ContentStage.ART_DIRECTION, ArtAgent()),
    AgentStep(ContentStage.VIDEO_CREATION, VideoAgent()),
    AgentStep(ContentStage.EDITING, EditingAgent()),
    GateStep(GateType.FINAL_VIDEO_REVIEW),  # Gate4 成片审核：自动
    AgentStep(ContentStage.OPERATION, OperationAgent()),
    GateStep(GateType.PRE_PUBLISH_REVIEW),  # Gate5 发布前审核：强制人工
]
