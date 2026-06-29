"""视频生成（Ark Seedance）适配器 + VideoAgent 集成测试（mock，不触网/不计费）。"""

import json

import pytest

from app.agents.base import AgentContext
from app.agents.video import VideoAgent
from app.integrations.video_gen import VideoGenResult
from app.llm.adapters import CompletionResult
from app.schemas.deliverable import VideoAssetPayload

_PLAN_JSON = json.dumps(
    {
        "tool": "seedance",
        "clips": [{"prompt": "开箱", "duration_seconds": 5, "motion": "推近"}],
        "resolution": "1080x1920",
    }
)


class FakeLLM:
    async def chat(self, session, org_id, agent_code, messages):
        return CompletionResult(_PLAN_JSON, "deepseek-chat", 1, 1, 2), 0.0


class FakeVideo:
    """假视频适配器：提交即返回 task_id，轮询一次即成功。"""

    provider = "fake"

    def __init__(self):
        self.submitted = None

    async def submit(self, prompt, *, ratio="9:16", duration=5):
        self.submitted = prompt
        return "task-123"

    async def poll(self, task_id):
        return VideoGenResult(task_id=task_id, status="succeeded", video_url="https://x/v.mp4")


_CTX = AgentContext(
    content_item_id=1,
    upstream={"art_prompt": {"visual_style": "冷调科技风", "prompts": ["开箱特写，冷光"]}},
)


@pytest.mark.asyncio
async def test_video_agent_attaches_real_video_url(monkeypatch):
    monkeypatch.setattr("app.config.settings.ark_api_key", "fake-key")
    monkeypatch.setattr("app.agents.video.settings.ark_api_key", "fake-key")
    fake_video = FakeVideo()
    agent = VideoAgent(llm=FakeLLM(), video=fake_video)

    result = await agent.run(None, 1, _CTX)
    assert isinstance(result, VideoAssetPayload)
    assert result.video_url == "https://x/v.mp4"
    assert result.gen_status == "succeeded"
    assert result.gen_task_id == "task-123"
    # 出片 prompt 取自上游美术提示词
    assert "开箱特写" in fake_video.submitted


@pytest.mark.asyncio
async def test_video_agent_degrades_without_key(monkeypatch):
    monkeypatch.setattr("app.agents.video.settings.ark_api_key", "")
    agent = VideoAgent(llm=FakeLLM(), video=FakeVideo())
    result = await agent.run(None, 1, _CTX)
    # 无 key：保留计划，不出片，不报错
    assert result.video_url is None
    assert result.tool == "seedance"


@pytest.mark.asyncio
async def test_video_agent_handles_gen_error(monkeypatch):
    monkeypatch.setattr("app.agents.video.settings.ark_api_key", "fake-key")

    class FailingVideo:
        provider = "fail"

        async def submit(self, prompt, *, ratio="9:16", duration=5):
            raise RuntimeError("ark down")

        async def poll(self, task_id):  # pragma: no cover
            ...

    agent = VideoAgent(llm=FakeLLM(), video=FailingVideo())
    result = await agent.run(None, 1, _CTX)
    # 出片失败不抛：保留计划，状态标错
    assert result.video_url is None
    assert result.gen_status.startswith("error:")
