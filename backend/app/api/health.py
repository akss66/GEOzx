"""系统健康检查路由。

- `/health`：存活探针（liveness），只要进程能响应就返回 200。
- `/health/ready`：就绪探针（readiness），实测 DB + Redis；任一不可用返回 503。
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.db import check_db, check_redis

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    """存活探针：进程在线即 ok。"""
    return {
        "status": "ok",
        "service": "dyflow-backend",
        "version": __version__,
        "environment": settings.environment,
    }


@router.get("/health/ready")
async def readiness() -> JSONResponse:
    """就绪探针：DB 与 Redis 均连通才算 ready。"""
    db_ok = await check_db()
    redis_ok = await check_redis()
    ready = db_ok and redis_ok
    payload = {
        "status": "ready" if ready else "degraded",
        "checks": {"db": db_ok, "redis": redis_ok},
    }
    return JSONResponse(payload, status_code=200 if ready else 503)
