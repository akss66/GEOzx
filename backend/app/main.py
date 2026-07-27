"""DyFlow 后端应用入口。

T2：接入配置、DB/Redis、Alembic 迁移基座；健康检查拆为存活 + 就绪两类。
后续里程碑挂载鉴权、编排、WebSocket、各业务路由。
"""

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import (
    account_data,
    accounts,
    agents,
    approvals,
    auth,
    brain,
    clients,
    costs,
    feedback,
    health,
    knowledge,
    knowledge_suggestions,
    llm,
    materials,
    matrix_distribution,
    metrics,
    model_configs,
    model_providers,
    notifications,
    orchestrator,
    platform_integrations,
    projects,
    publishing,
    risks,
    search,
    users,
    workspace_context,
    ws,
)
from app.config import settings

app = FastAPI(
    title="同舟行 API",
    version=__version__,
    description="同舟行 · 自媒体 AI 运营系统 后端",
)


def _json_safe_validation_detail(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe_validation_detail(item)
            for key, item in value.items()
            if key not in {"input", "body"}
        }
    if isinstance(value, list | tuple | set):
        return [_json_safe_validation_detail(item) for item in value]
    return str(value)


@app.exception_handler(RequestValidationError)
async def redact_request_validation_error(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": _json_safe_validation_detail(exc.errors())},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(approvals.router)
app.include_router(users.router)
app.include_router(clients.router)
app.include_router(workspace_context.router)
app.include_router(notifications.router)
app.include_router(search.router)
app.include_router(brain.router)
app.include_router(costs.router)
app.include_router(agents.router)
app.include_router(llm.router)
app.include_router(ws.router)
app.include_router(feedback.router)
app.include_router(orchestrator.router)
app.include_router(projects.router)
app.include_router(accounts.router)
app.include_router(account_data.router)
app.include_router(platform_integrations.router)
app.include_router(publishing.router)
app.include_router(model_configs.router)
app.include_router(model_configs.infrastructure_router)
app.include_router(model_providers.router)
app.include_router(knowledge.router)
app.include_router(knowledge_suggestions.router)
app.include_router(metrics.router)
app.include_router(matrix_distribution.router)
app.include_router(materials.router)
app.include_router(risks.router)


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs"}
