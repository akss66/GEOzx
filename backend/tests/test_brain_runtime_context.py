"""Concurrency guarantees for the in-process LangGraph session binding."""

import asyncio
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator.brain_runtime import _session_from_state, bind_runtime_session


@pytest.mark.asyncio
async def test_parallel_runtime_invocations_keep_their_own_database_session() -> None:
    first = cast(AsyncSession, object())
    second = cast(AsyncSession, object())
    ready = asyncio.Event()
    release = asyncio.Event()
    entered = 0

    async def resolve(bound: AsyncSession) -> AsyncSession:
        nonlocal entered
        with bind_runtime_session(bound):
            entered += 1
            if entered == 2:
                ready.set()
            await release.wait()
            async with _session_from_state({}) as resolved:
                return resolved

    first_task = asyncio.create_task(resolve(first))
    second_task = asyncio.create_task(resolve(second))
    await asyncio.wait_for(ready.wait(), timeout=1)
    release.set()

    resolved_first, resolved_second = await asyncio.gather(first_task, second_task)

    assert resolved_first is first
    assert resolved_second is second


@pytest.mark.asyncio
async def test_runtime_session_binding_is_cleared_after_the_invocation() -> None:
    session = cast(AsyncSession, object())

    with bind_runtime_session(session):
        async with _session_from_state({}) as resolved:
            assert resolved is session

    with pytest.raises(RuntimeError, match="active session is not bound"):
        _session_from_state({})
