"""Safe internal tool boundary used before exposing MCP integrations."""

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, User
from app.models.enums import UserRole


class ToolExecutionError(RuntimeError):
    """Base error for tool adapter failures."""


class ToolNotAllowedError(ToolExecutionError):
    """Raised when a tool is not registered or the user role is denied."""


class ToolValidationError(ToolExecutionError):
    """Raised when tool parameters fail schema validation."""


class ToolTimeoutError(ToolExecutionError):
    """Raised when a tool exceeds its timeout budget."""


class EmptyParams(BaseModel):
    """Default schema for tools that do not accept input."""


@dataclass(frozen=True)
class ToolExecutionContext:
    session: AsyncSession
    user: User


ToolHandler = Callable[
    [BaseModel, ToolExecutionContext],
    Awaitable[Mapping[str, Any]] | Mapping[str, Any],
]


@dataclass(frozen=True)
class ToolSpec:
    """Whitelisted internal tool definition.

    Tools are admin-only unless the caller explicitly narrows or expands allowed roles.
    """

    name: str
    handler: ToolHandler
    params_model: type[BaseModel] = EmptyParams
    allowed_roles: frozenset[UserRole] = field(default_factory=lambda: frozenset({UserRole.ADMIN}))
    timeout_seconds: float = 5.0


class ToolAdapter:
    """Validate, authorize, execute, and audit whitelisted internal tools."""

    def __init__(self, tools: list[ToolSpec] | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        if not tool.name:
            raise ValueError("tool name is required")
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        if tool.timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive")
        self._tools[tool.name] = tool

    def list_tools(self, user: User) -> list[str]:
        return sorted(name for name, tool in self._tools.items() if user.role in tool.allowed_roles)

    async def invoke(
        self,
        name: str,
        params: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        start = time.monotonic()
        tool = self._tools.get(name)
        if tool is None:
            await self._audit(
                context,
                name,
                "denied",
                params,
                start,
                error="tool is not whitelisted",
            )
            raise ToolNotAllowedError(f"tool is not whitelisted: {name}")

        if context.user.role not in tool.allowed_roles:
            await self._audit(context, name, "denied", params, start, error="role is not allowed")
            raise ToolNotAllowedError(f"role is not allowed to invoke tool: {name}")

        try:
            parsed = tool.params_model.model_validate(params)
        except ValidationError as exc:
            await self._audit(context, name, "invalid", params, start, error=str(exc))
            raise ToolValidationError(f"invalid params for tool: {name}") from exc

        try:
            result = await asyncio.wait_for(
                self._run_handler(tool.handler, parsed, context),
                timeout=tool.timeout_seconds,
            )
        except TimeoutError as exc:
            await self._audit(context, name, "timeout", params, start, error="tool timed out")
            raise ToolTimeoutError(f"tool timed out: {name}") from exc
        except Exception as exc:
            await self._audit(context, name, "error", params, start, error=str(exc))
            raise

        await self._audit(context, name, "ok", params, start, result=result)
        return result

    async def _run_handler(
        self,
        handler: ToolHandler,
        params: BaseModel,
        context: ToolExecutionContext,
    ) -> Mapping[str, Any]:
        result = handler(params, context)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _audit(
        self,
        context: ToolExecutionContext,
        tool_name: str,
        status: str,
        params: Mapping[str, Any],
        start: float,
        *,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        latency_ms = int((time.monotonic() - start) * 1000)
        context.session.add(
            Event(
                type="tool.invocation",
                payload={
                    "tool": tool_name,
                    "status": status,
                    "user_id": context.user.id,
                    "org_id": context.user.org_id,
                    "role": context.user.role.value,
                    "param_keys": sorted(params.keys()),
                    "result_keys": sorted(result.keys()) if result else [],
                    "error": error,
                    "latency_ms": latency_ms,
                },
            )
        )
        await context.session.commit()
