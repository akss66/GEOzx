"""视频生成后台任务逻辑（worker 调用）。

流程：取交付物的生成计划 → 提交 Ark → 轮询 → 下载落本地卷 → 记 MaterialAsset →
回写交付物 payload（video_url 指向本地播放接口）→ 发事件刷新看板。
与 arq 解耦，便于单测注入 fake 适配器/会话。
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.video_gen import VideoGenAdapter
from app.integrations.video_gen.ark import ArkVideoAdapter
from app.models import ContentItem, Deliverable, MaterialAsset, Project
from app.models.enums import DeliverableType, MaterialStatus

log = logging.getLogger("dyflow.video_gen")

_POLL_INTERVAL_S = 8
_POLL_MAX_TRIES = 30  # 约 4 分钟上限


def _build_prompt(payload: dict) -> str | None:
    """从视频交付物 payload 组装出片 prompt（优先 clips，其次 notes）。"""
    clips = payload.get("clips") or []
    if clips and isinstance(clips[0], dict):
        first = clips[0].get("prompt", "")
        if first:
            return str(first)
    return payload.get("notes") or None


async def generate_video_for_deliverable(
    session: AsyncSession,
    deliverable_id: int,
    *,
    video: VideoGenAdapter | None = None,
    emit=None,
    sleep=asyncio.sleep,
) -> MaterialAsset | None:
    """对某视频交付物执行真实出片，落本地卷并回写。

    返回 MaterialAsset（失败也返回带 error 的记录）。
    """
    deliverable = await session.get(Deliverable, deliverable_id)
    if deliverable is None or deliverable.type != DeliverableType.VIDEO_ASSET:
        return None

    prompt = _build_prompt(deliverable.payload)
    if not prompt:
        return None

    # org 解析
    ci = await session.get(ContentItem, deliverable.content_item_id)
    project = await session.get(Project, ci.project_id) if ci else None
    org_id = project.org_id if project else None

    adapter = video or ArkVideoAdapter()
    asset = MaterialAsset(
        org_id=org_id,
        content_item_id=deliverable.content_item_id,
        deliverable_id=deliverable.id,
        kind="video",
        provider=getattr(adapter, "provider", None),
        status=MaterialStatus.GENERATING,
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)

    try:
        task_id = await adapter.submit(prompt, ratio="9:16", duration=5)
        asset.external_task_id = task_id
        await session.commit()

        result = None
        for _ in range(_POLL_MAX_TRIES):
            result = await adapter.poll(task_id)
            if result.status in ("succeeded", "failed") or result.video_url:
                break
            await sleep(_POLL_INTERVAL_S)

        if result is None or result.status == "failed" or not result.video_url:
            raise RuntimeError(result.error if result else "生成超时")

        # 下载落本地卷
        from app.core import storage

        data = await adapter.download(result.video_url)
        rel_path = f"videos/{deliverable.content_item_id}/{asset.id}.mp4"
        _, size = storage.save_bytes(rel_path, data)

        asset.source_url = result.video_url
        asset.local_path = rel_path
        asset.size_bytes = size
        asset.status = MaterialStatus.READY

        # 回写交付物：video_url 指向本地播放接口
        payload = dict(deliverable.payload)
        payload["video_url"] = f"/materials/{asset.id}/file"
        payload["gen_task_id"] = task_id
        payload["gen_status"] = "ready"
        deliverable.payload = payload
        await session.commit()

        if emit:
            await emit(
                "video.ready",
                {"deliverable_id": deliverable.id, "material_id": asset.id},
                content_item_id=deliverable.content_item_id,
            )
        log.info("视频出片完成 asset=%s size=%s", asset.id, size)
        return asset

    except Exception as exc:  # noqa: BLE001 — 记录失败状态，不抛（后台任务）
        asset.status = MaterialStatus.FAILED
        asset.error = str(exc)
        payload = dict(deliverable.payload)
        payload["gen_status"] = f"error: {exc}"
        deliverable.payload = payload
        await session.commit()
        if emit:
            await emit(
                "video.failed",
                {"deliverable_id": deliverable.id, "error": str(exc)},
                content_item_id=deliverable.content_item_id,
            )
        log.warning("视频出片失败 deliverable=%s: %s", deliverable_id, exc)
        return asset
