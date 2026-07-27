"""Build traceable operating evidence without inventing missing facts."""

from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccountMetricSnapshot, PlatformContentRecord
from app.schemas.ai_coo import (
    AccountSituationOut,
    EvidenceRef,
    EvidenceTimeRange,
)

ACCOUNT_METRICS = (
    "follower_count",
    "follower_delta",
    "total_play",
    "total_exposure",
    "engagement_rate",
)


def _at_utc_midnight(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _freshness(value: date, now: datetime) -> str:
    age_days = (now.date() - value).days
    return "fresh" if age_days <= 7 else "stale"


async def build_account_situation(
    session: AsyncSession,
    *,
    org_id: int,
    account_id: int,
) -> AccountSituationOut:
    """Return only facts that can be traced to persisted account data."""
    now = datetime.now(UTC)
    latest_metric = await session.scalar(
        select(AccountMetricSnapshot)
        .where(
            AccountMetricSnapshot.org_id == org_id,
            AccountMetricSnapshot.account_id == account_id,
        )
        .order_by(AccountMetricSnapshot.stat_date.desc(), AccountMetricSnapshot.id.desc())
        .limit(1)
    )
    content_count = int(
        await session.scalar(
            select(func.count(PlatformContentRecord.id)).where(
                PlatformContentRecord.org_id == org_id,
                PlatformContentRecord.account_id == account_id,
            )
        )
        or 0
    )
    latest_content = await session.scalar(
        select(PlatformContentRecord)
        .where(
            PlatformContentRecord.org_id == org_id,
            PlatformContentRecord.account_id == account_id,
        )
        .order_by(
            PlatformContentRecord.published_at.desc(),
            PlatformContentRecord.id.desc(),
        )
        .limit(1)
    )

    evidence: list[EvidenceRef] = []
    if latest_metric is not None:
        collected_at = latest_metric.updated_at
        if collected_at.tzinfo is None:
            collected_at = collected_at.replace(tzinfo=UTC)
        for metric in ACCOUNT_METRICS:
            value = getattr(latest_metric, metric)
            if value is None:
                continue
            evidence.append(
                EvidenceRef(
                    source_type="account_metric_snapshot",
                    source_id=str(latest_metric.id),
                    metric=metric,
                    value=value,
                    time_range=EvidenceTimeRange(
                        start=latest_metric.stat_date.isoformat(),
                        end=latest_metric.stat_date.isoformat(),
                    ),
                    collected_at=collected_at,
                    freshness=_freshness(latest_metric.stat_date, now),
                )
            )

    if content_count > 0 and latest_content is not None:
        collected_at = latest_content.updated_at
        if collected_at.tzinfo is None:
            collected_at = collected_at.replace(tzinfo=UTC)
        evidence.append(
            EvidenceRef(
                source_type="platform_content_record",
                source_id=str(latest_content.id),
                metric="content_record_count",
                value=content_count,
                time_range=EvidenceTimeRange(
                    start=None,
                    end=(
                        latest_content.published_at.isoformat()
                        if latest_content.published_at
                        else None
                    ),
                ),
                collected_at=collected_at,
                freshness=(
                    _freshness(latest_content.published_at.date(), now)
                    if latest_content.published_at
                    else "unknown"
                ),
            )
        )

    missing_data: list[str] = []
    if latest_metric is None:
        missing_data.append("账号指标快照")
    if content_count == 0:
        missing_data.append("历史作品数据")

    if not evidence:
        return AccountSituationOut(
            account_id=account_id,
            generated_at=now,
            data_sufficiency="insufficient",
            conclusion="数据不足",
            diagnosis=[],
            evidence_refs=[],
            missing_data=missing_data,
            confidence=Decimal("0"),
        )

    sufficiency = "sufficient" if not missing_data else "partial"
    confidence = Decimal("0.80") if sufficiency == "sufficient" else Decimal("0.45")
    return AccountSituationOut(
        account_id=account_id,
        generated_at=now,
        data_sufficiency=sufficiency,
        conclusion="已建立真实数据基线",
        diagnosis=[],
        evidence_refs=evidence,
        missing_data=missing_data,
        confidence=confidence,
    )
