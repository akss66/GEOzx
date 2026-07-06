"""风险队列 API：整合质量门、授权异常、模型失败、平台回流失败。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.db import get_session
from app.models import Account, ContentItem, GateApproval, LLMCall
from app.models.enums import GateStatus
from app.schemas.risks import RiskQueueItem

router = APIRouter(prefix="/risks", tags=["risks"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


GATE_LABEL = {
    "positioning_review": "定位审核",
    "topic_review": "选题审核",
    "script_compliance": "脚本合规",
    "final_video_review": "成片审核",
    "pre_publish_review": "发布前审核",
    "large_ad_spend": "大额投放",
}

PLATFORM_LABEL = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "shipinhao": "视频号",
}


@router.get("/queue", response_model=list[RiskQueueItem])
async def list_risk_queue(user: CurrentUser, session: SessionDep) -> list[RiskQueueItem]:
    items: list[RiskQueueItem] = []

    gate_rows = (
        await session.execute(
            select(GateApproval, ContentItem.title)
            .join(ContentItem, GateApproval.content_item_id == ContentItem.id)
            .where(GateApproval.status == GateStatus.PENDING)
            .order_by(GateApproval.id)
        )
    ).all()
    for gate, title in gate_rows:
        label = GATE_LABEL.get(gate.gate.value, gate.gate.value)
        severity = (
            "high"
            if gate.gate.value in {"script_compliance", "pre_publish_review"}
            else "medium"
        )
        items.append(
            RiskQueueItem(
                id=f"gate:{gate.id}",
                category="quality_gate",
                severity=severity,
                title=f"{label}待审批",
                description=f"内容《{title}》正在等待人工质量门处理。",
                source=f"内容 #{gate.content_item_id}",
                status=gate.status.value,
                created_at=gate.created_at,
            )
        )

    accounts = (
        await session.scalars(
            select(Account).where(Account.org_id == user.org_id).order_by(Account.id)
        )
    ).all()
    for account in accounts:
        platform = PLATFORM_LABEL.get(account.platform.value, account.platform.value)
        if account.auth_status == "expired":
            items.append(
                RiskQueueItem(
                    id=f"account-auth:{account.id}",
                    category="account_auth",
                    severity="high",
                    title=f"{account.nickname}授权过期",
                    description=f"{platform}账号授权已过期，分发、数据回流或评论客服可能不可用。",
                    source=platform,
                    status=account.auth_status,
                    created_at=account.updated_at,
                )
            )
        if account.data_sync_status == "failed":
            items.append(
                RiskQueueItem(
                    id=f"data-sync:{account.id}",
                    category="data_sync",
                    severity="medium",
                    title=f"{account.nickname}平台回流失败",
                    description=f"{platform}账号数据回流失败，复盘与风险判断可能滞后。",
                    source=platform,
                    status=account.data_sync_status,
                    created_at=account.updated_at,
                )
            )

    failed_calls = (
        await session.scalars(
            select(LLMCall)
            .where(LLMCall.org_id == user.org_id, LLMCall.status == "error")
            .order_by(LLMCall.id.desc())
            .limit(20)
        )
    ).all()
    for call in failed_calls:
        agent = call.agent_code or "unknown"
        items.append(
            RiskQueueItem(
                id=f"model:{call.id}",
                category="model_failure",
                severity="high",
                title=f"{agent}模型调用失败",
                description=call.error or "模型调用失败，需检查模型、额度或网络。",
                source=call.model,
                status=call.status,
                created_at=call.created_at,
            )
        )

    return sorted(
        items,
        key=lambda item: (severity_order(item.severity), item.created_at),
        reverse=True,
    )


def severity_order(severity: str) -> int:
    if severity == "high":
        return 3
    if severity == "medium":
        return 2
    return 1
