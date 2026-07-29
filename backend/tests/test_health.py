"""健康检查测试。

- 存活探针 `/health` 与根路由：无需任何外部依赖。
- 就绪探针 `/health/ready`：用 monkeypatch 替换连通性检查，单测就绪/降级两种结果，
  不依赖真实 Postgres/Redis（真实连通由 docker compose 的容器健康检查覆盖）。
"""

from fastapi.testclient import TestClient

import app.api.health as health_mod
from app.main import app

client = TestClient(app)


def test_health_liveness() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store, private"
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "dyflow-backend"


def test_root_ok() -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["name"] == "同舟行"


def test_readiness_ok(monkeypatch) -> None:
    async def _ok() -> bool:
        return True

    monkeypatch.setattr(health_mod, "check_db", _ok)
    monkeypatch.setattr(health_mod, "check_redis", _ok)

    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"db": True, "redis": True}


def test_readiness_degraded_when_db_down(monkeypatch) -> None:
    async def _db_down() -> bool:
        return False

    async def _ok() -> bool:
        return True

    monkeypatch.setattr(health_mod, "check_db", _db_down)
    monkeypatch.setattr(health_mod, "check_redis", _ok)

    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["checks"]["db"] is False
    assert body["checks"]["redis"] is True
