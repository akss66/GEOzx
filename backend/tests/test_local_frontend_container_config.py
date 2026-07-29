from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_local_frontend_uses_http_only_nginx_config() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    local_nginx = REPO_ROOT / "frontend" / "nginx.local.conf"

    assert "NGINX_CONF: nginx.local.conf" in compose
    assert local_nginx.exists()

    nginx_config = local_nginx.read_text(encoding="utf-8")
    assert "listen 80;" in nginx_config
    assert "ssl_certificate" not in nginx_config
    assert "https://tzxai.top" not in nginx_config
    assert "proxy_pass http://backend:8000/" in nginx_config


def test_production_frontend_keeps_https_certificate_mount() -> None:
    compose = (REPO_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    nginx_config = (REPO_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")

    assert "/opt/dyflow/certs:/etc/nginx/ssl:ro" in compose
    assert "backend:\n        condition: service_healthy" in compose
    assert "listen 443 ssl;" in nginx_config
    assert "ssl_certificate /etc/nginx/ssl/tzxai.top.pem;" in nginx_config
