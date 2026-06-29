"""01 账号定位专家（真实 LLMAgent）。

system prompt: prompts/01-positioning.md（草稿，待配置表校准）
输出: PositioningStrategyPayload（schemas/deliverable.py）
"""

from app.agents.base import LLMAgent
from app.models.enums import DeliverableType


class PositioningAgent(LLMAgent):
    code = "01-positioning"
    output_type = DeliverableType.POSITIONING_STRATEGY
    prompt_name = "01-positioning"
