"""Safe internal tool boundary used before exposing MCP integrations."""

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

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


class ToolPermissionRequired(ToolExecutionError):
    """Raised when a controlled tool has not received explicit approval."""


class EmptyParams(BaseModel):
    """Default schema for tools that do not accept input."""


@dataclass(frozen=True)
class ToolExecutionContext:
    session: AsyncSession
    user: User
    project_id: int | None = None
    account_id: int | None = None
    task_id: int | None = None
    invocation_id: int | None = None
    approved: bool = False


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
    permission_mode: Literal["auto", "confirm", "manual", "disabled"] = "auto"
    scope: Literal["organization", "project", "account"] = "organization"
    redacted_result_fields: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "access_token",
                "refresh_token",
                "client_secret",
                "api_key",
                "authorization",
                "password",
            }
        )
    )


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

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

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

        scope_error = self._scope_error(tool, params, context)
        if scope_error is not None:
            await self._audit(context, name, "denied", params, start, error=scope_error)
            raise ToolNotAllowedError(scope_error)

        if tool.permission_mode == "disabled":
            await self._audit(context, name, "denied", params, start, error="tool is disabled")
            raise ToolNotAllowedError(f"tool is disabled: {name}")

        if tool.permission_mode in {"confirm", "manual"} and not context.approved:
            await self._audit(
                context,
                name,
                "waiting_approval",
                params,
                start,
                error="explicit approval is required",
            )
            raise ToolPermissionRequired(f"explicit approval is required for tool: {name}")

        declared_fields = set(tool.params_model.model_fields)
        undeclared_fields = set(params) - declared_fields
        if undeclared_fields:
            await self._audit(
                context,
                name,
                "invalid",
                params,
                start,
                error="undeclared parameters are not allowed",
            )
            raise ToolValidationError(f"invalid params for tool: {name}")

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

        safe_result = _redact_mapping(result, tool.redacted_result_fields)
        await self._audit(context, name, "ok", params, start, result=safe_result)
        return safe_result

    @staticmethod
    def _scope_error(
        tool: ToolSpec,
        params: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> str | None:
        if tool.scope == "project" and context.project_id is None:
            return "project-scoped tool requires the selected project"
        if tool.scope == "account" and context.account_id is None:
            return "account-scoped tool requires the selected account"
        if "project_id" in params and params["project_id"] != context.project_id:
            return "tool project scope does not match the selected project"
        if "account_id" in params and params["account_id"] != context.account_id:
            return "tool account scope does not match the selected account"
        return None

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


def _redact_mapping(
    value: Mapping[str, Any],
    redacted_fields: frozenset[str],
) -> dict[str, Any]:
    def redact(item: Any, key: str | None = None) -> Any:
        if key is not None and key.lower() in redacted_fields:
            return "[REDACTED]"
        if isinstance(item, Mapping):
            return {
                str(child_key): redact(child_value, str(child_key))
                for child_key, child_value in item.items()
            }
        if isinstance(item, list):
            return [redact(child) for child in item]
        return item

    return redact(value)
