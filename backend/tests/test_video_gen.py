"""视频生成测试：VideoAgent 只产计划 + 后台出片任务（mock 适配器，不触网/不计费）。"""

import json

import pytest
from sqlalchemy import select

from app.agents.base import AgentContext
from app.agents.video import VideoAgent
from app.integrations.video_gen import VideoGenResult
from app.integrations.video_gen.tasks import generate_video_for_deliverable
from app.llm.adapters import CompletionResult
from app.models import ContentItem, Deliverable, MaterialAsset, Org, Project
from app.models.enums import DeliverableStatus, DeliverableType, MaterialStatus
from app.schemas.deliverable import VideoAssetPayload

_PLAN_JSON = json.dumps(
    {
        "tool": "seedance",
        "clips": [{"prompt": "开箱特写，冷光科技风", "duration_seconds": 5, "motion": "推近"}],
        "resolution": "1080x1920",
    }
)


class FakeLLM:
    async def chat(self, session, org_id, agent_code, messages):
        return CompletionResult(_PLAN_JSON, "deepseek-chat", 1, 1, 2), 0.0


@pytest.mark.asyncio
async def test_video_agent_only_plans_marks_queued():
    """VideoAgent 只产计划并标记 queued，不触网出片。"""
    agent = VideoAgent(llm=FakeLLM())
    ctx = AgentContext(content_item_id=1, upstream={})
    result = await agent.run(None, 1, ctx)
    assert isinstance(result, VideoAssetPayload)
    assert result.gen_status == "queued"
    assert result.video_url is None


class FakeVideo:
    """假视频适配器：提交→轮询一次成功→下载返回固定字节。"""

    provider = "fake"

    async def submit(self, prompt, *, ratio="9:16", duration=5):
        return "task-xyz"

    async def poll(self, task_id):
        return VideoGenResult(task_id=task_id, status="succeeded", video_url="https://x/v.mp4")

    async def download(self, url):
        return b"FAKE_MP4_BYTES"


async def _seed_video_deliverable(session) -> Deliverable:
    org = Org(name="O")
    project = Project(org=org, name="P")
    ci = ContentItem(project=project, title="测试")
    session.add(ci)
    await session.flush()
    d = Deliverable(
        content_item_id=ci.id,
        agent_code="04-video",
        type=DeliverableType.VIDEO_ASSET,
        version=1,
        status=DeliverableStatus.DRAFT,
        payload={
            "tool": "seedance",
            "clips": [{"prompt": "开箱", "duration_seconds": 5}],
            "resolution": "1080x1920",
            "gen_status": "queued",
        },
    )
    session.add(d)
    await session.commit()
    await session.refresh(d)
    return d


@pytest.mark.asyncio
async def test_generate_video_downloads_and_records(session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    d = await _seed_video_deliverable(session)

    events: list = []

    async def emit(t, p=None, content_item_id=None, project_id=None):
        events.append(t)

    asset = await generate_video_for_deliverable(
        session, d.id, video=FakeVideo(), emit=emit, sleep=_noop_sleep
    )

    assert asset.status == MaterialStatus.READY
    assert asset.local_path.endswith(".mp4")
    assert asset.size_bytes == len(b"FAKE_MP4_BYTES")
    # 落本地卷
    assert (tmp_path / asset.local_path).read_bytes() == b"FAKE_MP4_BYTES"
    # 回写交付物 video_url 指向本地播放接口
    await session.refresh(d)
    assert d.payload["video_url"] == f"/materials/{asset.id}/file"
    assert d.payload["gen_status"] == "ready"
    assert "video.ready" in events


@pytest.mark.asyncio
async def test_generate_video_handles_failure(session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.storage_local_dir", str(tmp_path))
    d = await _seed_video_deliverable(session)

    class Failing:
        provider = "fail"

        async def submit(self, prompt, *, ratio="9:16", duration=5):
            raise RuntimeError("ark down")

        async def poll(self, task_id): ...
        async def download(self, url): ...

    events: list = []

    async def emit(t, p=None, content_item_id=None, project_id=None):
        events.append(t)

    asset = await generate_video_for_deliverable(
        session, d.id, video=Failing(), emit=emit, sleep=_noop_sleep
    )
    assert asset.status == MaterialStatus.FAILED
    assert "ark down" in asset.error
    assert "video.failed" in events
    # 仅一条素材记录
    count = len((await session.scalars(select(MaterialAsset))).all())
    assert count == 1


async def _noop_sleep(_seconds):
    return None
