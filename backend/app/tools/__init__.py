"""Internal tool adapter boundary for future MCP-style integrations."""

from app.tools.adapter import (
    ToolAdapter,
    ToolExecutionContext,
    ToolExecutionError,
    ToolNotAllowedError,
    ToolPermissionRequired,
    ToolSpec,
    ToolTimeoutError,
    ToolValidationError,
)

__all__ = [
    "ToolAdapter",
    "ToolExecutionContext",
    "ToolExecutionError",
    "ToolNotAllowedError",
    "ToolPermissionRequired",
    "ToolSpec",
    "ToolTimeoutError",
    "ToolValidationError",
]
