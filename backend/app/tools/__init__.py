"""Internal tool adapter boundary for future MCP-style integrations."""

from app.tools.adapter import (
    ToolAdapter,
    ToolExecutionContext,
    ToolExecutionError,
    ToolNotAllowedError,
    ToolSpec,
    ToolTimeoutError,
    ToolValidationError,
)

__all__ = [
    "ToolAdapter",
    "ToolExecutionContext",
    "ToolExecutionError",
    "ToolNotAllowedError",
    "ToolSpec",
    "ToolTimeoutError",
    "ToolValidationError",
]
