"""04 视频创作专家（真实 LLMAgent）。

system prompt: prompts/04-video.md ｜ 输出: VideoAssetPayload ｜ 输入: 上游美术提示词。
真实出片由 Seedance 适配器执行（M1 E7）；此处先产生成参数计划。
"""

from app.agents.base import LLMAgent
from app.models.enums import DeliverableType


class VideoAgent(LLMAgent):
    code = "04-video"
    output_type = DeliverableType.VIDEO_ASSET
    prompt_name = "04-video"
