"""Fail-closed DB-only freshness adapter boundary for checkpoint reuse."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, ContentItem, Deliverable
from app.orchestrator.checkpoint_graph_contracts import CheckpointStepSpec
from app.orchestrator.runtime_scope import RuntimeScope
from app.schemas.run_revision import FreshnessStamp, StageDataEnvelope


class FreshnessValidator(Protocol):
    key: str

    async def current_stamp(
        self,
        session: AsyncSession,
        *,
        scope: RuntimeScope,
        step: CheckpointStepSpec,
        input: StageDataEnvelope,
        db_now: datetime,
    ) -> FreshnessStamp: ...


@dataclass(frozen=True)
class FreshnessVerdict:
    kind: Literal["reusable", "full_recompute"]
    reason: str | None
    validated_at: datetime | None = None
    stamp: FreshnessStamp | None = None


class _AccountDatabaseFreshnessValidator:
    """A bounded watermark over authoritative account-owned database rows."""

    def __init__(self, key: str, *, ttl: timedelta) -> None:
        self.key = key
        self._ttl = ttl

    async def current_stamp(
        self,
        session: AsyncSession,
        *,
        scope: RuntimeScope,
        step: CheckpointStepSpec,
        input: StageDataEnvelope,
        db_now: datetime,
    ) -> FreshnessStamp:
        from app.services.checkpoint_hashing import canonical_json_sha256

        account = (
            await session.execute(
                select(
                    Account.id,
                    Account.org_id,
                    Account.platform,
                    Account.nickname,
                    Account.status,
                    Account.updated_at,
                ).where(Account.id == scope.account_id)
            )
        ).one_or_none()
        if account is None or account.org_id != scope.org_id:
            raise RuntimeError("freshness account scope does not exist")
        content_rows = tuple(
            (
                await session.execute(
                    select(ContentItem.id, ContentItem.updated_at)
                    .where(ContentItem.account_id == scope.account_id)
                    .order_by(ContentItem.id)
                )
            ).all()
        )
        deliverable_rows = tuple(
            (
                await session.execute(
                    select(Deliverable.id, Deliverable.version, Deliverable.updated_at)
                    .join(ContentItem, ContentItem.id == Deliverable.content_item_id)
                    .where(ContentItem.account_id == scope.account_id)
                    .order_by(Deliverable.id)
                )
            ).all()
        )
        watermark = canonical_json_sha256(
            domain="checkpoint-freshness-watermark/v1",
            value={
                "policy_key": self.key,
                "account": {
                    "id": account.id,
                    "platform": account.platform.value,
                    "nickname": account.nickname,
                    "status": account.status.value,
                    "updated_at": _as_utc(account.updated_at),
                },
                "content_rows": [
                    {"id": row.id, "updated_at": _as_utc(row.updated_at)} for row in content_rows
                ],
                "deliverable_rows": [
                    {
                        "id": row.id,
                        "version": row.version,
                        "updated_at": _as_utc(row.updated_at),
                    }
                    for row in deliverable_rows
                ],
                "input": input.model_dump(mode="python"),
            },
        )
        return FreshnessStamp(
            policy_key=self.key,
            watermark_hash=watermark,
            expires_at=db_now + self._ttl,
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# Validators are deliberately server-owned and query only the current database transaction.
_VALIDATORS: dict[str, FreshnessValidator] = {
    "account-snapshot/v1": _AccountDatabaseFreshnessValidator(
        "account-snapshot/v1", ttl=timedelta(minutes=15)
    ),
    "benchmark-evidence/v1": _AccountDatabaseFreshnessValidator(
        "benchmark-evidence/v1", ttl=timedelta(hours=1)
    ),
}


def get_freshness_validator(key: str) -> FreshnessValidator | None:
    return _VALIDATORS.get(key)


def require_freshness_validator(key: str) -> FreshnessValidator:
    validator = get_freshness_validator(key)
    if validator is None:
        raise LookupError(f"freshness_validator_missing:{key}")
    return validator


async def load_transaction_db_now(session: AsyncSession) -> datetime:
    dialect_name = session.bind.dialect.name if session.bind is not None else ""
    clock = (
        func.transaction_timestamp()
        if dialect_name == "postgresql"
        else func.current_timestamp()
    )
    value = (await session.execute(select(clock))).scalar_one()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise RuntimeError("database transaction clock returned an invalid value")
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def assess_checkpoint_freshness(
    session: AsyncSession,
    *,
    scope: RuntimeScope,
    step: CheckpointStepSpec,
    input: StageDataEnvelope,
    source_stamp: FreshnessStamp | None,
) -> FreshnessVerdict:
    if step.reuse_policy != "freshness_bound":
        return FreshnessVerdict(kind="reusable", reason=None)
    policy_key = step.freshness_policy_key
    validator = get_freshness_validator(policy_key or "")
    if validator is None:
        return FreshnessVerdict(kind="full_recompute", reason="freshness_validator_missing")
    if source_stamp is None or source_stamp.policy_key != policy_key:
        return FreshnessVerdict(kind="full_recompute", reason="freshness_stamp_missing")
    db_now = await load_transaction_db_now(session)
    current = await validator.current_stamp(
        session,
        scope=scope,
        step=step,
        input=input,
        db_now=db_now,
    )
    if db_now > source_stamp.expires_at:
        return FreshnessVerdict(
            kind="full_recompute",
            reason="freshness_expired",
            validated_at=db_now,
            stamp=current,
        )
    if current.policy_key != policy_key or current.watermark_hash != source_stamp.watermark_hash:
        return FreshnessVerdict(
            kind="full_recompute",
            reason="freshness_watermark_changed",
            validated_at=db_now,
            stamp=current,
        )
    return FreshnessVerdict(
        kind="reusable",
        reason=None,
        validated_at=db_now,
        stamp=current,
    )


__all__ = [
    "FreshnessValidator",
    "FreshnessVerdict",
    "assess_checkpoint_freshness",
    "get_freshness_validator",
    "load_transaction_db_now",
    "require_freshness_validator",
]
