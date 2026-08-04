"""Per-account execution lanes for provider write side effects.

PostgreSQL uses a transaction-scoped advisory lock held by an independent
guard session.  The caller may therefore commit its durable dispatch receipt
and final outcome while the account lane remains exclusively owned.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.models import Account, AgentRun
from app.services.runtime_locking import acquire_runtime_run_gate

_WRITE_KINDS = frozenset({"idempotent_write", "non_idempotent_write"})
_SQLITE_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}


class AccountExecutionLaneConflict(RuntimeError):
    """A write did not have a complete, valid account execution scope."""


class AccountExecutionLeaseLost(AccountExecutionLaneConflict):
    """The caller no longer owns a current running AgentRun lease."""


@dataclass(frozen=True)
class AccountExecutionGuard:
    """User-safe evidence that the account lane was acquired."""

    org_id: int
    account_id: int
    run_id: int
    wait_ms: int


@asynccontextmanager
async def account_execution_lane(
    account_id: int | None,
    operation_kind: str,
    *,
    run_id: int | None,
    execution_owner: str | None,
    _session_factory: Any = None,
    _allow_test_fallback: bool = False,
) -> AsyncIterator[AccountExecutionGuard | None]:
    """Acquire the production account lane around one real adapter dispatch.

    Reads intentionally do not open a session or issue advisory SQL.  Writes
    fail closed unless their account, Run, and current lease owner are all
    explicit.  ``_session_factory`` is an internal test/integration seam; the
    production default is the application's independent session factory.
    """

    if operation_kind == "read":
        yield None
        return
    if operation_kind not in _WRITE_KINDS:
        raise AccountExecutionLaneConflict("unsupported account operation kind")
    if (
        isinstance(account_id, bool)
        or not isinstance(account_id, int)
        or account_id <= 0
        or isinstance(run_id, bool)
        or not isinstance(run_id, int)
        or run_id <= 0
        or not isinstance(execution_owner, str)
        or not execution_owner.strip()
    ):
        raise AccountExecutionLaneConflict(
            "account writes require account, run, and execution owner"
        )

    session_factory = _session_factory or async_session
    async with session_factory() as probe:
        run, account = await _validated_scope(
            probe,
            account_id=account_id,
            run_id=run_id,
            execution_owner=execution_owner,
        )
        org_id = run.org_id
        dialect = probe.get_bind().dialect.name
        assert account.org_id == org_id

    started = monotonic()
    if dialect != "postgresql":
        if not _allow_test_fallback:
            raise AccountExecutionLaneConflict(
                "account write execution requires PostgreSQL"
            )
        lock = _SQLITE_LOCKS.setdefault((org_id, account_id), asyncio.Lock())
        async with lock:
            async with session_factory() as validation_session:
                await _validated_scope(
                    validation_session,
                    account_id=account_id,
                    run_id=run_id,
                    execution_owner=execution_owner,
                )
            yield AccountExecutionGuard(
                org_id=org_id,
                account_id=account_id,
                run_id=run_id,
                wait_ms=max(0, int((monotonic() - started) * 1000)),
            )
        return

    async with session_factory() as guard_session:
        async with guard_session.begin():
            # The production global lock order is Run gate first, account lane
            # second.  The advisory Run gate blocks takeover/cancellation while
            # still allowing the heartbeat's AgentRun row update to proceed.
            await acquire_runtime_run_gate(guard_session, (run_id,))
            await _validated_scope(
                guard_session,
                account_id=account_id,
                run_id=run_id,
                execution_owner=execution_owner,
            )
            await guard_session.scalar(
                select(
                    func.pg_advisory_xact_lock(
                        account_execution_lock_key(org_id, account_id)
                    )
                )
            )
            # A queued waiter can spend time behind another account write.  It
            # must revalidate ownership after it reaches the front of the lane.
            await _validated_scope(
                guard_session,
                account_id=account_id,
                run_id=run_id,
                execution_owner=execution_owner,
                populate_existing=True,
            )
            yield AccountExecutionGuard(
                org_id=org_id,
                account_id=account_id,
                run_id=run_id,
                wait_ms=max(0, int((monotonic() - started) * 1000)),
            )


async def _validated_scope(
    session: AsyncSession,
    *,
    account_id: int,
    run_id: int,
    execution_owner: str,
    populate_existing: bool = False,
) -> tuple[AgentRun, Account]:
    run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.id == run_id)
        .execution_options(populate_existing=populate_existing)
    )
    account = await session.scalar(
        select(Account)
        .where(Account.id == account_id)
        .execution_options(populate_existing=populate_existing)
    )
    if run is None or account is None or run.org_id != account.org_id:
        raise AccountExecutionLaneConflict("account execution scope does not match")
    if (
        run.status != "running"
        or run.lease_owner != execution_owner
        or not _is_future(run.leased_until)
    ):
        raise AccountExecutionLeaseLost("account execution lease is no longer owned")
    return run, account


def _is_future(value: datetime | None) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value > datetime.now(UTC)


def account_execution_lock_key(org_id: int, account_id: int) -> int:
    """Map the tenant/account tuple deterministically to signed PostgreSQL bigint."""

    digest = hashlib.blake2b(
        f"{org_id}:{account_id}".encode("ascii"),
        digest_size=8,
        person=b"geozx-acct-lane",
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


__all__ = [
    "AccountExecutionGuard",
    "AccountExecutionLaneConflict",
    "AccountExecutionLeaseLost",
    "account_execution_lock_key",
    "account_execution_lane",
]
