"""04 视频创作专家（真实 LLMAgent + Ark 真实出片）。

system prompt: prompts/04-video.md ｜ 输出: VideoAssetPayload ｜ 输入: 上游美术提示词。
流程：LLM 产生成参数计划 → 用美术提示词调 Ark（豆包 Seedance）真实出片 → 视频 URL 写入交付物。
Ark 失败或未配置时降级：保留计划、video_url 留空，不中断流水线。
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import AgentContext, LLMAgent
from app.config import settings
from app.integrations.video_gen import VideoGenAdapter
from app.integrations.video_gen.ark import ArkVideoAdapter
from app.models.enums import DeliverableType
from app.schemas.deliverable import DeliverablePayload

# 轮询参数：最多等约 3 分钟（视频生成异步，通常 40~90s）。
_POLL_INTERVAL_S = 8
_POLL_MAX_TRIES = 24


class VideoAgent(LLMAgent):
    code = "04-video"
    output_type = DeliverableType.VIDEO_ASSET
    prompt_name = "04-video"

    def __init__(self, llm=None, video: VideoGenAdapter | None = None) -> None:
        super().__init__(llm)
        self._video = video or ArkVideoAdapter()

    async def run(
        self, session: AsyncSession, org_id: int | None, ctx: AgentContext
    ) -> DeliverablePayload:
        payload = await super().run(session, org_id, ctx)

        # 取美术提示词作为出片输入；无 key / 无提示词则跳过真实生成
        prompt = self._gen_prompt(ctx)
        if not settings.ark_api_key or not prompt:
            return payload

        try:
            task_id = await self._video.submit(prompt, ratio="9:16", duration=5)
            result = await self._wait(task_id)
            payload.gen_task_id = result.task_id
            payload.gen_status = result.status
            payload.video_url = result.video_url
        except Exception as exc:  # noqa: BLE001 — 出片失败不阻断流水线，记录状态
            payload.gen_status = f"error: {exc}"
        return payload

    def _gen_prompt(self, ctx: AgentContext) -> str | None:
        """从上游美术提示词组装生成 prompt（取首条 + 风格）。"""
        art = ctx.upstream.get(DeliverableType.ART_PROMPT.value)
        if not art:
            return None
        prompts = art.get("prompts") or []
        style = art.get("visual_style", "")
        first = prompts[0] if prompts else ""
        return f"{first}，{style}".strip("，") or None

    async def _wait(self, task_id: str):
        result = None
        for _ in range(_POLL_MAX_TRIES):
            result = await self._video.poll(task_id)
            if result.status in ("succeeded", "failed") or result.video_url:
                return result
            await asyncio.sleep(_POLL_INTERVAL_S)
        return result  # 超时返回最后一次状态（running）
