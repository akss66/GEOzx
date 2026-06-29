"""素材路由：播放本地视频文件 + 列出素材。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.auth import CurrentUser
from app.db import get_session
from app.models import MaterialAsset
from app.models.enums import MaterialStatus

router = APIRouter(prefix="/materials", tags=["materials"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{material_id}/file")
async def get_material_file(material_id: int, session: SessionDep) -> FileResponse:
    """播放/下载素材本地文件（卷内）。无 token：供 <video> 标签直接引用。"""
    asset = await session.get(MaterialAsset, material_id)
    if asset is None or asset.status != MaterialStatus.READY or not asset.local_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材不存在或未就绪")
    path = storage.resolve(asset.local_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材文件丢失")
    return FileResponse(path, media_type="video/mp4")


@router.get("/{material_id}")
async def get_material(material_id: int, user: CurrentUser, session: SessionDep) -> dict:
    """素材元信息（状态/大小/任务等）。"""
    asset = await session.get(MaterialAsset, material_id)
    if asset is None or asset.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材不存在")
    return {
        "id": asset.id,
        "kind": asset.kind,
        "provider": asset.provider,
        "status": asset.status.value,
        "size_bytes": asset.size_bytes,
        "deliverable_id": asset.deliverable_id,
        "file_url": f"/materials/{asset.id}/file" if asset.status == MaterialStatus.READY else None,
    }
