"""复盘看板路由：指标录入（回流入口）+ 聚合视图。"""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import MetricSnapshot
from app.schemas.metrics import (
    EngagementPoint,
    IngestMetricRequest,
    RankItem,
    ReviewOverview,
    TrendPoint,
)

router = APIRouter(prefix="/metrics", tags=["metrics"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_metric(
    body: IngestMetricRequest, user: CurrentUser, session: SessionDep
) -> dict:
    """录入一条指标快照（E8 抖音回流与手动录入共用入口）。

    同 (content_item, stat_date) 已存在则更新，否则新建（幂等回流）。
    """
    existing = None
    if body.content_item_id is not None:
        existing = await session.scalar(
            select(MetricSnapshot).where(
                MetricSnapshot.content_item_id == body.content_item_id,
                MetricSnapshot.stat_date == body.stat_date,
            )
        )
    fields = body.model_dump()
    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        snap = existing
    else:
        snap = MetricSnapshot(org_id=user.org_id, **fields)
        session.add(snap)
    await session.commit()
    await session.refresh(snap)
    return {"id": snap.id}


@router.get("/overview", response_model=ReviewOverview)
async def review_overview(
    user: CurrentUser,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> ReviewOverview:
    """复盘聚合：近 N 天趋势 + 完播互动 + 内容排名 + 汇总。无数据时 has_data=False。"""
    since = date.today() - timedelta(days=days - 1)
    base = MetricSnapshot.org_id == user.org_id

    # 趋势 + 完播互动：按日期聚合
    by_date = (
        await session.execute(
            select(
                MetricSnapshot.stat_date,
                func.sum(MetricSnapshot.play),
                func.sum(MetricSnapshot.exposure),
                func.avg(MetricSnapshot.completion_rate),
                func.avg(MetricSnapshot.like_rate),
            )
            .where(base, MetricSnapshot.stat_date >= since)
            .group_by(MetricSnapshot.stat_date)
            .order_by(MetricSnapshot.stat_date)
        )
    ).all()

    trend = [
        TrendPoint(date=d.strftime("%m/%d"), play=int(play or 0), exposure=int(exp or 0))
        for d, play, exp, _comp, _like in by_date
    ]
    engagement = [
        EngagementPoint(
            date=d.strftime("%m/%d"),
            completion_rate=round(float(comp or 0), 4),
            like_rate=round(float(like or 0), 4),
        )
        for d, _play, _exp, comp, like in by_date
    ]

    # 内容排名：按标题聚合平均完播率（标题为空的快照不参与排名）
    by_content = (
        await session.execute(
            select(
                MetricSnapshot.title,
                func.avg(MetricSnapshot.completion_rate),
            )
            .where(
                base,
                MetricSnapshot.stat_date >= since,
                MetricSnapshot.title.isnot(None),
            )
            .group_by(MetricSnapshot.title)
        )
    ).all()
    ranked = sorted(
        (
            RankItem(title=title or "未命名", completion_rate=round(float(comp or 0), 4))
            for title, comp in by_content
        ),
        key=lambda r: r.completion_rate,
        reverse=True,
    )

    total_play = sum(t.play for t in trend)
    avg_completion = (
        round(sum(e.completion_rate for e in engagement) / len(engagement), 4)
        if engagement
        else 0.0
    )
    follower_delta = (
        await session.scalar(
            select(func.coalesce(func.sum(MetricSnapshot.follower_delta), 0)).where(
                base, MetricSnapshot.stat_date >= since
            )
        )
    ) or 0

    return ReviewOverview(
        has_data=bool(by_date),
        trend=trend,
        engagement=engagement,
        rank_top=ranked[:3],
        rank_bottom=ranked[-3:][::-1] if len(ranked) > 3 else [],
        total_play=total_play,
        avg_completion_rate=avg_completion,
        follower_delta=int(follower_delta),
    )
