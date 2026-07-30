import asyncio

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.models import AgentToolCall, BrainTask, ToolExecutionAttempt
from app.models.enums import BrainTaskStatus, BrainTaskType, UserRole
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.brain import RuntimeToolCall
from app.tools import (
    ToolAdapter,
    ToolExecutionContext,
    ToolSpec,
    ToolTimeoutError,
)


class EchoParams(BaseModel):
    message: str


async def _task(session, admin) -> BrainTask:
    task = BrainTask(
        org_id=admin.org_id,
        created_by_id=admin.id,
        title="Tool runtime",
        type=BrainTaskType.CONTENT_CREATION,
        status=BrainTaskStatus.RUNNING,
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


@pytest.mark.asyncio
async def test_tool_execution_is_durable_and_idempotent(session, admin) -> None:
    calls = 0

    async def handler(params: EchoParams, _context: ToolExecutionContext) -> dict:
        nonlocal calls
        calls += 1
        return {"echo": params.message}

    adapter = ToolAdapter(
        [
            ToolSpec(
                name="diagnostics.echo",
                handler=handler,
                params_model=EchoParams,
                side_effect_level="read",
                allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
            )
        ]
    )
    task = await _task(session, admin)
    request = RuntimeToolCall(
        tool_code="diagnostics.echo",
        arguments={"message": "hello"},
        purpose="verify runtime",
        idempotency_key="round-1-echo",
    )
    executor = DurableToolExecutor(adapter)

    first = await executor.execute(task=task, user=admin, request=request)
    second = await executor.execute(task=task, user=admin, request=request)

    assert first.status == "success"
    assert second.status == "success"
    assert first.result == second.result == {"echo": "hello"}
    assert first.tool_call.id == second.tool_call.id
    assert calls == 1


@pytest.mark.asyncio
async def test_controlled_tool_creates_permission_before_execution(session, admin) -> None:
    calls = 0

    async def handler(params: EchoParams, _context: ToolExecutionContext) -> dict:
        nonlocal calls
        calls += 1
        return {"echo": params.message}

    adapter = ToolAdapter(
        [
            ToolSpec(
                name="publish.prepare",
                handler=handler,
                params_model=EchoParams,
                side_effect_level="read",
                permission_mode="confirm",
            )
        ]
    )
    task = await _task(session, admin)
    request = RuntimeToolCall(
        tool_code="publish.prepare",
        arguments={"message": "package"},
        purpose="prepare publish package",
        idempotency_key="publish-package-1",
    )
    executor = DurableToolExecutor(adapter)

    pending = await executor.execute(task=task, user=admin, request=request)
    assert pending.status == "waiting_approval"
    assert pending.result is None
    assert calls == 0

    approved = await executor.execute(
        task=task,
        user=admin,
        request=request,
        approved=True,
    )
    assert approved.status == "success"
    assert approved.result == {"echo": "package"}
    assert calls == 1

    rows = list(
        await session.scalars(
            select(AgentToolCall).where(AgentToolCall.task_id == task.id)
        )
    )
    assert len(rows) == 1
    assert rows[0].requires_human_confirmation is True


@pytest.mark.asyncio
async def test_idempotent_write_retries_with_one_server_provider_key(
    session,
    admin,
) -> None:
    provider_keys: list[str | None] = []

    async def handler(
        params: EchoParams,
        context: ToolExecutionContext,
    ) -> dict:
        provider_keys.append(context.provider_idempotency_key)
        if len(provider_keys) == 1:
            await asyncio.sleep(0.05)
        return {"echo": params.message}

    adapter = ToolAdapter(
        [
            ToolSpec(
                name="provider.upsert",
                handler=handler,
                params_model=EchoParams,
                side_effect_level="idempotent_write",
                timeout_seconds=0.001,
            )
        ]
    )
    task = await _task(session, admin)
    request = RuntimeToolCall(
        tool_code="provider.upsert",
        arguments={"message": "hello"},
        purpose="upsert provider record",
        idempotency_key="logical-upsert-1",
    )
    executor = DurableToolExecutor(adapter)

    with pytest.raises(ToolTimeoutError):
        await executor.execute(task=task, user=admin, request=request)
    retried = await executor.execute(task=task, user=admin, request=request)

    assert retried.status == "success"
    assert len(provider_keys) == 2
    assert provider_keys[0]
    assert provider_keys[0] == provider_keys[1]
    assert provider_keys[0] != request.idempotency_key
    attempts = list(
        await session.scalars(
            select(ToolExecutionAttempt)
            .where(ToolExecutionAttempt.tool_call_id == retried.tool_call.id)
            .order_by(ToolExecutionAttempt.attempt_no)
        )
    )
    assert [attempt.status for attempt in attempts] == ["failed", "success"]
    assert {attempt.provider_idempotency_key for attempt in attempts} == {
        provider_keys[0]
    }


@pytest.mark.asyncio
async def test_non_idempotent_timeout_is_ambiguous_and_never_replayed(
    session,
    admin,
) -> None:
    calls = 0

    async def handler(
        _params: EchoParams,
        context: ToolExecutionContext,
    ) -> dict:
        nonlocal calls
        calls += 1
        assert context.provider_idempotency_key
        await asyncio.sleep(0.05)
        return {"published": True}

    adapter = ToolAdapter(
        [
            ToolSpec(
                name="provider.publish",
                handler=handler,
                params_model=EchoParams,
                side_effect_level="non_idempotent_write",
                timeout_seconds=0.001,
            )
        ]
    )
    task = await _task(session, admin)
    request = RuntimeToolCall(
        tool_code="provider.publish",
        arguments={"message": "publish once"},
        purpose="publish once",
        idempotency_key="logical-publish-1",
    )
    executor = DurableToolExecutor(adapter)

    first = await executor.execute(task=task, user=admin, request=request)
    replay = await executor.execute(task=task, user=admin, request=request)

    assert first.status == replay.status == "ambiguous"
    assert first.tool_call.id == replay.tool_call.id
    assert calls == 1
    attempts = list(
        await session.scalars(
            select(ToolExecutionAttempt).where(
                ToolExecutionAttempt.tool_call_id == first.tool_call.id
            )
        )
    )
    assert len(attempts) == 1
    assert attempts[0].status == "ambiguous"


@pytest.mark.asyncio
async def test_non_idempotent_local_commit_failure_is_ambiguous(
    session,
    admin,
    monkeypatch,
) -> None:
    calls = 0

    async def handler(
        _params: EchoParams,
        _context: ToolExecutionContext,
    ) -> dict:
        nonlocal calls
        calls += 1
        return {"published": True}

    executor = DurableToolExecutor(
        ToolAdapter(
            [
                ToolSpec(
                    name="provider.publish",
                    handler=handler,
                    params_model=EchoParams,
                    side_effect_level="non_idempotent_write",
                )
            ]
        )
    )
    task = await _task(session, admin)
    request = RuntimeToolCall(
        tool_code="provider.publish",
        arguments={"message": "publish once"},
        purpose="publish once",
        idempotency_key="logical-publish-commit-fail",
    )
    real_commit = session.commit
    commit_count = 0

    async def fail_result_commit_once() -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise RuntimeError("simulated local commit failure")
        await real_commit()

    monkeypatch.setattr(session, "commit", fail_result_commit_once)

    first = await executor.execute(task=task, user=admin, request=request)
    replay = await executor.execute(task=task, user=admin, request=request)

    assert first.status == replay.status == "ambiguous"
    assert calls == 1
    attempt = await session.scalar(
        select(ToolExecutionAttempt).where(
            ToolExecutionAttempt.tool_call_id == first.tool_call.id
        )
    )
    assert attempt is not None
    assert attempt.status == "ambiguous"
    assert attempt.error == "LocalCommitFailed"


@pytest.mark.asyncio
async def test_non_idempotent_reentrant_dispatch_uses_one_tool_call(
    session,
    admin,
) -> None:
    calls = 0
    nested_statuses: list[str] = []
    task = await _task(session, admin)
    request = RuntimeToolCall(
        tool_code="provider.publish",
        arguments={"message": "publish once"},
        purpose="publish once",
        idempotency_key="logical-publish-reentrant",
    )
    executor: DurableToolExecutor

    async def handler(
        _params: EchoParams,
        _context: ToolExecutionContext,
    ) -> dict:
        nonlocal calls
        calls += 1
        nested = await executor.execute(task=task, user=admin, request=request)
        nested_statuses.append(nested.status)
        return {"published": True}

    executor = DurableToolExecutor(
        ToolAdapter(
            [
                ToolSpec(
                    name="provider.publish",
                    handler=handler,
                    params_model=EchoParams,
                    side_effect_level="non_idempotent_write",
                )
            ]
        )
    )

    result = await executor.execute(task=task, user=admin, request=request)

    assert result.status == "success"
    assert nested_statuses == ["running"]
    assert calls == 1
    rows = list(
        await session.scalars(
            select(AgentToolCall).where(AgentToolCall.task_id == task.id)
        )
    )
    assert len(rows) == 1
