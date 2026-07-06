import asyncio

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from app.models import Event
from app.models.enums import UserRole
from app.tools import (
    ToolAdapter,
    ToolExecutionContext,
    ToolNotAllowedError,
    ToolSpec,
    ToolTimeoutError,
    ToolValidationError,
)


class EchoParams(BaseModel):
    message: str


async def echo_handler(params: EchoParams, _context: ToolExecutionContext) -> dict:
    return {"echo": params.message}


async def slow_handler(_params: EchoParams, _context: ToolExecutionContext) -> dict:
    await asyncio.sleep(0.05)
    return {"done": True}


@pytest.mark.asyncio
async def test_unregistered_tool_is_denied_and_audited(session, admin) -> None:
    adapter = ToolAdapter()

    with pytest.raises(ToolNotAllowedError):
        await adapter.invoke("shell.run", {}, ToolExecutionContext(session=session, user=admin))

    event = await session.scalar(select(Event).where(Event.type == "tool.invocation"))
    assert event is not None
    assert event.payload["tool"] == "shell.run"
    assert event.payload["status"] == "denied"
    assert event.payload["error"] == "tool is not whitelisted"


@pytest.mark.asyncio
async def test_role_must_be_allowed(session, member) -> None:
    called = False

    async def handler(params: EchoParams, _context: ToolExecutionContext) -> dict:
        nonlocal called
        called = True
        return {"echo": params.message}

    adapter = ToolAdapter(
        [ToolSpec(name="diagnostics.echo", handler=handler, params_model=EchoParams)]
    )

    with pytest.raises(ToolNotAllowedError):
        await adapter.invoke(
            "diagnostics.echo",
            {"message": "hi"},
            ToolExecutionContext(session=session, user=member),
        )

    assert called is False
    event = await session.scalar(select(Event).where(Event.type == "tool.invocation"))
    assert event.payload["status"] == "denied"
    assert event.payload["role"] == UserRole.USER.value


@pytest.mark.asyncio
async def test_params_are_validated_before_execution(session, admin) -> None:
    adapter = ToolAdapter(
        [ToolSpec(name="diagnostics.echo", handler=echo_handler, params_model=EchoParams)]
    )

    with pytest.raises(ToolValidationError):
        await adapter.invoke(
            "diagnostics.echo",
            {"wrong": "shape"},
            ToolExecutionContext(session=session, user=admin),
        )

    event = await session.scalar(select(Event).where(Event.type == "tool.invocation"))
    assert event.payload["status"] == "invalid"
    assert event.payload["param_keys"] == ["wrong"]


@pytest.mark.asyncio
async def test_allowed_tool_executes_and_records_audit(session, admin) -> None:
    adapter = ToolAdapter(
        [
            ToolSpec(
                name="diagnostics.echo",
                handler=echo_handler,
                params_model=EchoParams,
                allowed_roles=frozenset({UserRole.ADMIN, UserRole.USER}),
            )
        ]
    )

    result = await adapter.invoke(
        "diagnostics.echo",
        {"message": "ok"},
        ToolExecutionContext(session=session, user=admin),
    )

    assert result == {"echo": "ok"}
    event = await session.scalar(select(Event).where(Event.type == "tool.invocation"))
    assert event.payload["status"] == "ok"
    assert event.payload["result_keys"] == ["echo"]
    assert event.payload["param_keys"] == ["message"]


@pytest.mark.asyncio
async def test_tool_timeout_is_audited(session, admin) -> None:
    adapter = ToolAdapter(
        [
            ToolSpec(
                name="diagnostics.slow",
                handler=slow_handler,
                params_model=EchoParams,
                timeout_seconds=0.001,
            )
        ]
    )

    with pytest.raises(ToolTimeoutError):
        await adapter.invoke(
            "diagnostics.slow",
            {"message": "wait"},
            ToolExecutionContext(session=session, user=admin),
        )

    event = await session.scalar(select(Event).where(Event.type == "tool.invocation"))
    assert event.payload["status"] == "timeout"
