"""Real PostgreSQL barriers for the production account execution lane."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import Account, AgentRun, BrainTask, Org, ToolExecutionAttempt, User
from app.models.enums import BrainTaskStatus, BrainTaskType, Platform, UserRole
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.brain import RuntimeToolCall
from app.services.account_execution_lane import (
    account_execution_lane,
    account_execution_lock_key,
)
from app.tools import ToolAdapter, ToolExecutionContext, ToolSpec


class _Params(BaseModel):
    value: str


def _postgres_url() -> str:
    return os.environ["TEST_POSTGRES_URL"].replace(
        "postgresql+psycopg://", "postgresql+asyncpg://"
    ).replace("postgresql://", "postgresql+asyncpg://")


async def _wait_for_account_advisory_wait(
    sessions,
    *,
    lock_key: int,
    deadline_seconds: float = 10,
) -> None:
    unsigned_key = lock_key & ((1 << 64) - 1)
    class_id = unsigned_key >> 32
    object_id = unsigned_key & 0xFFFFFFFF
    deadline = monotonic() + deadline_seconds
    async with sessions() as monitor:
        while monotonic() < deadline:
            waiting = await monitor.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks "
                    "WHERE locktype = 'advisory' AND NOT granted "
                    "AND classid::bigint = :class_id "
                    "AND objid::bigint = :object_id AND objsubid = 1)"
                ),
                {"class_id": class_id, "object_id": object_id},
            )
            if waiting:
                return
            await asyncio.sleep(0.01)
    raise AssertionError("writer never waited on the account advisory gate")


async def _scope(sessions, *, suffix: str):
    async with sessions() as setup:
        org = Org(name=f"account-lane-{suffix}")
        admin = User(
            org=org,
            email=f"account-lane-{suffix}@test.invalid",
            hashed_password="unused",
            display_name="Account lane",
            role=UserRole.ADMIN,
        )
        setup.add(admin)
        await setup.flush()
        account = Account(
            org_id=org.id,
            nickname=f"account-lane-{suffix}",
            platform=Platform.DOUYIN,
            auth={},
        )
        task = BrainTask(
            org_id=org.id,
            created_by_id=admin.id,
            title="Account lane PostgreSQL",
            type=BrainTaskType.CONTENT_CREATION,
            status=BrainTaskStatus.RUNNING,
        )
        runs = [
            AgentRun(
                org_id=org.id,
                requested_by_id=admin.id,
                task_id=None,
                client_message_id=f"account-lane:{suffix}:{number}",
                status="running",
                phase="running",
                lease_owner=f"pg-worker-{number}",
                leased_until=datetime.now(UTC) + timedelta(minutes=5),
            )
            for number in (1, 2)
        ]
        setup.add_all([account, task, *runs])
        await setup.flush()
        for run in runs:
            run.task_id = task.id
        await setup.commit()
        return admin.id, account.id, task.id, tuple(run.id for run in runs)


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the account execution lane barrier",
)
def test_postgres_same_account_executor_claims_once_and_waits_on_advisory_gate() -> None:
    async def exercise() -> None:
        engine = create_async_engine(
            _postgres_url(),
            connect_args={
                "server_settings": {
                    "lock_timeout": "10000",
                    "statement_timeout": "20000",
                }
            },
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        suffix = uuid4().hex
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            user_id, account_id, task_id, run_ids = await _scope(
                sessions, suffix=suffix
            )
            adapter_entered = asyncio.Event()
            release_adapter = asyncio.Event()
            calls = 0

            async def handler(
                params: _Params,
                _context: ToolExecutionContext,
            ) -> dict:
                nonlocal calls
                calls += 1
                adapter_entered.set()
                await release_adapter.wait()
                return {"value": params.value}

            executor = DurableToolExecutor(
                ToolAdapter(
                    [
                        ToolSpec(
                            name="provider.account_upsert",
                            handler=handler,
                            params_model=_Params,
                            side_effect_level="idempotent_write",
                        )
                    ]
                )
            )
            request = RuntimeToolCall(
                tool_code="provider.account_upsert",
                arguments={"value": "one"},
                purpose="prove one account dispatch",
                idempotency_key=f"account-upsert:{suffix}",
            )

            async def execute_once(run_id: int, owner: str):
                async with sessions() as execution:
                    user = await execution.get(User, user_id)
                    task = await execution.get(BrainTask, task_id)
                    assert user is not None and task is not None
                    return await executor.execute(
                        task=task,
                        user=user,
                        request=request,
                        account_id=account_id,
                        run_id=run_id,
                        execution_owner=owner,
                    )

            first = asyncio.create_task(execute_once(run_ids[0], "pg-worker-1"))
            await asyncio.wait_for(adapter_entered.wait(), timeout=5)
            second = asyncio.create_task(execute_once(run_ids[1], "pg-worker-2"))
            async with sessions() as lookup:
                account = await lookup.get(Account, account_id)
                assert account is not None
                lock_key = account_execution_lock_key(account.org_id, account.id)
            await _wait_for_account_advisory_wait(
                sessions,
                lock_key=lock_key,
            )
            assert second.done() is False
            release_adapter.set()
            first_outcome, second_outcome = await asyncio.wait_for(
                asyncio.gather(first, second), timeout=10
            )
            assert first_outcome.status == second_outcome.status == "success"
            assert first_outcome.tool_call.id == second_outcome.tool_call.id
            assert calls == 1
            async with sessions() as verify:
                attempts = list(
                    await verify.scalars(
                        select(ToolExecutionAttempt).where(
                            ToolExecutionAttempt.tool_call_id
                            == first_outcome.tool_call.id
                        )
                    )
                )
                assert attempts
                assert all(
                    attempt.status in {"success", "failed", "ambiguous"}
                    and attempt.finished_at is not None
                    for attempt in attempts
                )
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="TEST_POSTGRES_URL is required for the account execution lane barrier",
)
def test_postgres_different_accounts_and_reads_bypass_an_occupied_lane() -> None:
    async def exercise() -> None:
        engine = create_async_engine(_postgres_url())
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        first_suffix = uuid4().hex
        second_suffix = uuid4().hex
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            _, first_account, _, first_runs = await _scope(
                sessions, suffix=first_suffix
            )
            _, second_account, _, second_runs = await _scope(
                sessions, suffix=second_suffix
            )
            release = asyncio.Event()
            first_entered = asyncio.Event()
            second_entered = asyncio.Event()

            async def hold_first() -> None:
                async with account_execution_lane(
                    first_account,
                    "idempotent_write",
                    run_id=first_runs[0],
                    execution_owner="pg-worker-1",
                    _session_factory=sessions,
                ):
                    first_entered.set()
                    await release.wait()

            async def enter_second() -> None:
                await first_entered.wait()
                async with account_execution_lane(
                    second_account,
                    "non_idempotent_write",
                    run_id=second_runs[0],
                    execution_owner="pg-worker-1",
                    _session_factory=sessions,
                ):
                    second_entered.set()
                    await release.wait()

            first = asyncio.create_task(hold_first())
            second = asyncio.create_task(enter_second())
            await asyncio.wait_for(second_entered.wait(), timeout=5)
            async with account_execution_lane(
                first_account,
                "read",
                run_id=None,
                execution_owner=None,
                _session_factory=sessions,
            ) as read_guard:
                assert read_guard is None
            release.set()
            await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
        finally:
            await engine.dispose()

    asyncio.run(exercise())
