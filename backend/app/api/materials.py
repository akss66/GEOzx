"""素材路由：播放本地视频文件 + 列出素材。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.auth import CurrentUser
from app.db import get_session
from app.models import MaterialAsset
from app.models.enums import MaterialStatus
from app.schemas.material import MaterialAssetOut

router = APIRouter(prefix="/materials", tags=["materials"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _material_out(asset: MaterialAsset) -> MaterialAssetOut:
    return MaterialAssetOut(
        id=asset.id,
        content_item_id=asset.content_item_id,
        deliverable_id=asset.deliverable_id,
        kind=asset.kind,
        provider=asset.provider,
        status=asset.status,
        size_bytes=asset.size_bytes,
        file_url=f"/materials/{asset.id}/file" if asset.status == MaterialStatus.READY else None,
        error=asset.error,
        created_at=asset.created_at,
    )


@router.get("", response_model=list[MaterialAssetOut])
async def list_materials(
    user: CurrentUser,
    session: SessionDep,
    content_item_id: Annotated[int | None, Query()] = None,
) -> list[MaterialAssetOut]:
    query = select(MaterialAsset).where(MaterialAsset.org_id == user.org_id)
    if content_item_id is not None:
        query = query.where(MaterialAsset.content_item_id == content_item_id)
    rows = (await session.scalars(query.order_by(MaterialAsset.id.desc()))).all()
    return [_material_out(asset) for asset in rows]


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
    return _material_out(asset).model_dump(mode="json")
