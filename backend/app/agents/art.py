"""03 美术指导提示词专家（真实 LLMAgent）。

system prompt: prompts/03-art.md ｜ 输出: ArtPromptPayload ｜ 输入: 上游脚本。
"""

from app.agents.base import LLMAgent
from app.models.enums import DeliverableType


class ArtAgent(LLMAgent):
    code = "03-art"
    output_type = DeliverableType.ART_PROMPT
    prompt_name = "03-art"
