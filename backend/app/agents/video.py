"""04 视频创作专家（真实 LLMAgent，只产生成计划）。

system prompt: Prompt Registry `experts/04-video/v1.md`
输出: VideoAssetPayload ｜ 输入: 上游美术提示词。
真实出片由后台 arq 任务异步执行（agent.done → generate_video，见 E7 异步化）：
此处仅产生成参数计划并标记 gen_status=queued，避免同步阻塞编排/触发 HTTP 超时。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext, LLMAgent
from app.models.enums import DeliverableType
from app.schemas.deliverable import VideoAssetPayload


class VideoAgent(LLMAgent):
    code = "04-video"
    output_type = DeliverableType.VIDEO_ASSET
    prompt_name = "04-video"

    async def run(
        self, session: AsyncSession, org_id: int | None, ctx: AgentContext
    ) -> VideoAssetPayload:
        payload = await super().run(session, org_id, ctx)
        assert isinstance(payload, VideoAssetPayload)
        # 标记待出片：真实生成在后台任务完成后回写 video_url
        payload.gen_status = "queued"
        return payload
