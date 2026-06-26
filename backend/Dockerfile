# DyFlow 后端镜像。T1 仅安装运行依赖；DB/arq 等在后续里程碑加入并随 pyproject 增长。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# uv 负责依赖安装（跨平台、快）
RUN pip install --no-cache-dir uv

# 先装依赖（利用层缓存），再拷源码与迁移
COPY pyproject.toml ./
COPY app ./app
RUN uv pip install --system --no-cache .

# Alembic 迁移（在容器内执行 alembic upgrade head）
COPY alembic.ini ./
COPY migrations ./migrations

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=6 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
