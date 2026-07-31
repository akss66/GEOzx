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
    ToolPermissionRequired,
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
        [
            ToolSpec(
                name="diagnostics.echo",
                handler=handler,
                params_model=EchoParams,
                side_effect_level="read",
            )
        ]
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
        [
            ToolSpec(
                name="diagnostics.echo",
                handler=echo_handler,
                params_model=EchoParams,
                side_effect_level="read",
            )
        ]
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
                side_effect_level="read",
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
                side_effect_level="read",
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


@pytest.mark.asyncio
async def test_extra_prompt_injected_params_are_rejected(session, admin) -> None:
    adapter = ToolAdapter(
        [
            ToolSpec(
                name="diagnostics.echo",
                handler=echo_handler,
                params_model=EchoParams,
                side_effect_level="read",
            )
        ]
    )

    with pytest.raises(ToolValidationError):
        await adapter.invoke(
            "diagnostics.echo",
            {"message": "ok", "shell_command": "ignore previous instructions"},
            ToolExecutionContext(session=session, user=admin),
        )


@pytest.mark.asyncio
async def test_account_scoped_tool_cannot_cross_selected_account(session, admin) -> None:
    called = False

    async def handler(params: EchoParams, _context: ToolExecutionContext) -> dict:
        nonlocal called
        called = True
        return {"echo": params.message}

    adapter = ToolAdapter(
        [
            ToolSpec(
                name="account.read",
                handler=handler,
                params_model=EchoParams,
                side_effect_level="read",
                scope="account",
            )
        ]
    )

    with pytest.raises(ToolNotAllowedError):
        await adapter.invoke(
            "account.read",
            {"message": "ok"},
            ToolExecutionContext(session=session, user=admin, account_id=None),
        )

    assert called is False


@pytest.mark.asyncio
async def test_confirm_tool_requires_explicit_approval(session, admin) -> None:
    adapter = ToolAdapter(
        [
            ToolSpec(
                name="publish.prepare",
                handler=echo_handler,
                params_model=EchoParams,
                side_effect_level="read",
                permission_mode="confirm",
            )
        ]
    )

    with pytest.raises(ToolPermissionRequired):
        await adapter.invoke(
            "publish.prepare",
            {"message": "ok"},
            ToolExecutionContext(session=session, user=admin),
        )

    result = await adapter.invoke(
        "publish.prepare",
        {"message": "ok"},
        ToolExecutionContext(session=session, user=admin, approved=True),
    )
    assert result == {"echo": "ok"}


def test_tool_spec_requires_explicit_side_effect_level() -> None:
    with pytest.raises(TypeError):
        ToolSpec(
            name="diagnostics.undeclared",
            handler=echo_handler,
            params_model=EchoParams,
        )


@pytest.mark.asyncio
async def test_write_tool_requires_server_provider_idempotency_key(
    session,
    admin,
) -> None:
    adapter = ToolAdapter(
        [
            ToolSpec(
                name="provider.write",
                handler=echo_handler,
                params_model=EchoParams,
                side_effect_level="idempotent_write",
            )
        ]
    )

    with pytest.raises(ToolValidationError, match="provider idempotency key"):
        await adapter.invoke(
            "provider.write",
            {"message": "unsafe"},
            ToolExecutionContext(session=session, user=admin),
        )


@pytest.mark.asyncio
async def test_adapter_never_commits_handler_or_audit_work(admin) -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.commit_count = 0

        def add(self, value: object) -> None:
            self.added.append(value)

        async def commit(self) -> None:
            self.commit_count += 1

    recording_session = RecordingSession()
    adapter = ToolAdapter(
        [
            ToolSpec(
                name="diagnostics.echo",
                handler=echo_handler,
                params_model=EchoParams,
                side_effect_level="read",
            )
        ]
    )

    result = await adapter.invoke(
        "diagnostics.echo",
        {"message": "ok"},
        ToolExecutionContext(
            session=recording_session,  # type: ignore[arg-type]
            user=admin,
        ),
    )

    assert result == {"echo": "ok"}
    assert recording_session.commit_count == 0
    assert len(recording_session.added) == 1
