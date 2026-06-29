"""02 编导文案专家（真实 LLMAgent）。

system prompt: prompts/02-content.md（草稿，待配置表校准）
输出: VideoScriptPayload（schemas/deliverable.py）
输入: 上游定位策略（LLMAgent.build_user_message 自动注入 ctx.upstream）。
"""

from app.agents.base import LLMAgent
from app.models.enums import DeliverableType


class ContentAgent(LLMAgent):
    code = "02-content"
    output_type = DeliverableType.VIDEO_SCRIPT
    prompt_name = "02-content"
