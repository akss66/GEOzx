"""素材路由：播放本地视频文件 + 列出素材。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.auth import CurrentUser
from app.core.workspace_access import (
    accessible_account_clause,
    accessible_project_ids,
    require_content_scope,
)
from app.db import get_session
from app.models import Account, ContentItem, MaterialAsset
from app.models.enums import MaterialStatus, UserRole
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


async def _material_for_user(
    session: AsyncSession,
    material_id: int,
    user,
) -> MaterialAsset:
    asset = await session.get(MaterialAsset, material_id)
    if asset is None or asset.org_id != user.org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材不存在")
    if asset.content_item_id is None:
        if user.role != UserRole.ADMIN:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材不存在")
        return asset
    content_item = await session.get(ContentItem, asset.content_item_id)
    if content_item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材不存在")
    await require_content_scope(
        session,
        user,
        project_id=content_item.project_id,
        account_id=content_item.account_id,
    )
    return asset


@router.get("", response_model=list[MaterialAssetOut])
async def list_materials(
    user: CurrentUser,
    session: SessionDep,
    content_item_id: Annotated[int | None, Query()] = None,
) -> list[MaterialAssetOut]:
    query = select(MaterialAsset).where(MaterialAsset.org_id == user.org_id)
    if content_item_id is not None:
        content_item = await session.get(ContentItem, content_item_id)
        if content_item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="内容不存在")
        await require_content_scope(
            session,
            user,
            project_id=content_item.project_id,
            account_id=content_item.account_id,
        )
        query = query.where(MaterialAsset.content_item_id == content_item_id)
    elif user.role != UserRole.ADMIN:
        project_ids = await accessible_project_ids(session, user)
        visible_accounts = select(Account.id).where(
            await accessible_account_clause(session, user)
        )
        query = query.join(
            ContentItem,
            MaterialAsset.content_item_id == ContentItem.id,
        ).where(
            or_(
                ContentItem.project_id.in_(project_ids) if project_ids else false(),
                and_(
                    ContentItem.project_id.is_(None),
                    ContentItem.account_id.in_(visible_accounts),
                ),
            )
        )
    rows = (await session.scalars(query.order_by(MaterialAsset.id.desc()))).all()
    return [_material_out(asset) for asset in rows]


@router.get("/{material_id}/file")
async def get_material_file(
    material_id: int,
    user: CurrentUser,
    session: SessionDep,
) -> FileResponse:
    """播放或下载当前用户有权访问的本地素材。"""
    asset = await _material_for_user(session, material_id, user)
    if asset.status != MaterialStatus.READY or not asset.local_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材不存在或未就绪")
    path = storage.resolve(asset.local_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="素材文件丢失")
    return FileResponse(path)


@router.get("/{material_id}")
async def get_material(material_id: int, user: CurrentUser, session: SessionDep) -> dict:
    """素材元信息（状态/大小/任务等）。"""
    asset = await _material_for_user(session, material_id, user)
    return _material_out(asset).model_dump(mode="json")
