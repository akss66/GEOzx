"""05 剪辑专家（真实 LLMAgent）。

system prompt: prompts/05-editing.md ｜ 输出: EditedVideoPayload ｜ 输入: 上游素材计划。
"""

from app.agents.base import LLMAgent
from app.models.enums import DeliverableType


class EditingAgent(LLMAgent):
    code = "05-editing"
    output_type = DeliverableType.EDITED_VIDEO
    prompt_name = "05-editing"
