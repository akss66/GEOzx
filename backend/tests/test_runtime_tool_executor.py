import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.models import AgentToolCall, BrainTask
from app.models.enums import BrainTaskStatus, BrainTaskType, UserRole
from app.orchestrator.tool_executor import DurableToolExecutor
from app.schemas.brain import RuntimeToolCall
from app.tools import ToolAdapter, ToolExecutionContext, ToolSpec


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
