"""DyFlow 后端应用入口。

T2：接入配置、DB/Redis、Alembic 迁移基座；健康检查拆为存活 + 就绪两类。
后续里程碑挂载鉴权、编排、WebSocket、各业务路由。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import auth, events, health, llm, orchestrator, users, ws
from app.config import settings

app = FastAPI(
    title="DyFlow API",
    version=__version__,
    description="抖音自媒体运营 Agent 编排系统 后端",
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
app.include_router(users.router)
app.include_router(llm.router)
app.include_router(events.router)
app.include_router(ws.router)
app.include_router(orchestrator.router)


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs"}
