"""Shared rollout gate for the typed main-Agent runtime."""

from fastapi import HTTPException, status

from app.config import settings

MAIN_AGENT_V2_DISABLED_DETAIL = {
    "code": "MAIN_AGENT_V2_DISABLED",
    "message": "Main Agent V2 is disabled",
}
MAIN_AGENT_TYPED_RUNTIME_DISABLED_DETAIL = {
    "code": "MAIN_AGENT_TYPED_RUNTIME_DISABLED",
    "message": "Typed main agent runtime is disabled",
}


def require_main_agent_runtime_enabled() -> None:
    """Reject new typed-runtime API work unless both rollout gates are open."""

    if not settings.main_agent_v2_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=MAIN_AGENT_V2_DISABLED_DETAIL,
        )
    if not settings.main_agent_typed_runtime_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=MAIN_AGENT_TYPED_RUNTIME_DISABLED_DETAIL,
        )
