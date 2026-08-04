import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Account, AgentRun
from app.models.enums import BrainTaskStatus, Platform
from app.services.account_execution_lane import (
    AccountExecutionLeaseLost,
    account_execution_lane,
)
from app.services.runtime_state import brain_task_status


async def _active_run(session, admin, *, account_name: str, owner: str) -> tuple[Account, AgentRun]:
    account = Account(
        org_id=admin.org_id,
        nickname=account_name,
        platform=Platform.DOUYIN,
        auth={},
    )
    run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id=f"lane:{account_name}:{owner}",
        status="running",
        phase="running",
        lease_owner=owner,
        leased_until=datetime.now(UTC) + timedelta(minutes=5),
    )
    session.add_all([account, run])
    await session.commit()
    return account, run


def _sessions(session):
    assert session.bind is not None
    return async_sessionmaker(session.bind, expire_on_commit=False)


@pytest.mark.asyncio
async def test_same_account_writes_never_overlap(session, admin) -> None:
    account, first_run = await _active_run(
        session, admin, account_name="same-account", owner="worker-a"
    )
    second_run = AgentRun(
        org_id=admin.org_id,
        requested_by_id=admin.id,
        client_message_id="lane:same-account:worker-b",
        status="running",
        phase="running",
        lease_owner="worker-b",
        leased_until=datetime.now(UTC) + timedelta(minutes=5),
    )
    session.add(second_run)
    await session.commit()
    sessions = _sessions(session)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first_writer() -> None:
        async with account_execution_lane(
            account.id,
            "idempotent_write",
            run_id=first_run.id,
            execution_owner="worker-a",
            _session_factory=sessions,
        ):
            first_entered.set()
            await release_first.wait()

    async def second_writer() -> None:
        await first_entered.wait()
        async with account_execution_lane(
            account.id,
            "non_idempotent_write",
            run_id=second_run.id,
            execution_owner="worker-b",
            _session_factory=sessions,
        ):
            second_entered.set()

    first_task = asyncio.create_task(first_writer())
    second_task = asyncio.create_task(second_writer())
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    await asyncio.sleep(0.02)
    assert second_entered.is_set() is False
    release_first.set()
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=1)
    assert second_entered.is_set() is True


@pytest.mark.asyncio
async def test_different_account_writes_overlap(session, admin) -> None:
    first_account, first_run = await _active_run(
        session, admin, account_name="account-a", owner="worker-a"
    )
    second_account, second_run = await _active_run(
        session, admin, account_name="account-b", owner="worker-b"
    )
    sessions = _sessions(session)
    both_entered = asyncio.Event()
    entered = 0
    release = asyncio.Event()

    async def writer(account_id: int, run_id: int, owner: str) -> None:
        nonlocal entered
        async with account_execution_lane(
            account_id,
            "idempotent_write",
            run_id=run_id,
            execution_owner=owner,
            _session_factory=sessions,
        ):
            entered += 1
            if entered == 2:
                both_entered.set()
            await release.wait()

    tasks = [
        asyncio.create_task(writer(first_account.id, first_run.id, "worker-a")),
        asyncio.create_task(writer(second_account.id, second_run.id, "worker-b")),
    ]
    await asyncio.wait_for(both_entered.wait(), timeout=1)
    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_read_bypasses_an_occupied_write_lane(session, admin) -> None:
    account, run = await _active_run(
        session, admin, account_name="read-bypass", owner="worker-a"
    )
    sessions = _sessions(session)
    async with account_execution_lane(
        account.id,
        "idempotent_write",
        run_id=run.id,
        execution_owner="worker-a",
        _session_factory=sessions,
    ):
        async with account_execution_lane(
            account.id,
            "read",
            run_id=None,
            execution_owner=None,
            _session_factory=sessions,
        ) as guard:
            assert guard is None


@pytest.mark.asyncio
async def test_read_does_not_open_a_guard_session() -> None:
    class FailIfOpened:
        def __call__(self):
            raise AssertionError("read lanes must not create a guard session")

    async with account_execution_lane(
        None,
        "read",
        run_id=None,
        execution_owner=None,
        _session_factory=FailIfOpened(),
    ) as guard:
        assert guard is None


@pytest.mark.asyncio
async def test_old_worker_fails_closed_after_lease_owner_changes(session, admin) -> None:
    account, run = await _active_run(
        session, admin, account_name="lease-change", owner="worker-new"
    )

    with pytest.raises(AccountExecutionLeaseLost):
        async with account_execution_lane(
            account.id,
            "idempotent_write",
            run_id=run.id,
            execution_owner="worker-old",
            _session_factory=_sessions(session),
        ):
            raise AssertionError("stale worker must never enter the account lane")


def test_only_ambiguous_stop_projects_pending_confirmation() -> None:
    assert (
        brain_task_status("stopped", error_code="TOOL_RESULT_AMBIGUOUS")
        is BrainTaskStatus.PENDING_CONFIRMATION
    )
    assert brain_task_status("stopped", error_code="USER_STOPPED") is BrainTaskStatus.FAILED
