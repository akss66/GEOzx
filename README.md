# DyFlow

面向企业运营团队的 AI Agent 编排系统，在系统内编排 8 个 AI Agent，覆盖抖音自媒体运营全流程（账号定位 → 编导文案 → 美术提示词 → 视频创作 → 剪辑 → 运营分发 →（并行）投流 + 客服），数据/事件驱动的内容生产流水线 + 规模化执行层。

- 设计规格：[`SPEC.md`](SPEC.md)　·　产品/设计基调：[`PRODUCT.md`](PRODUCT.md)
- 实现计划：[`tasks/plan.md`](tasks/plan.md)　·　任务清单：[`tasks/todo.md`](tasks/todo.md)

## 技术栈

- 后端：Python 3.11 + FastAPI(async) + PostgreSQL 16 + Redis 7 + arq + SQLAlchemy/Alembic
- 前端：React 18 + TypeScript + Vite + Ant Design
- 编排：自研轻量状态机 + 事件驱动；多模型网关（v1 默认 DeepSeek）
- 部署：Docker Compose 单机一键（本机 Windows 自托管，可迁云）

## 快速开始（一键启动）

前置：Docker Desktop。

```bash
cp .env.example .env        # 按需填入 DEEPSEEK_API_KEY 等
docker compose up -d        # 拉起 postgres / redis / minio / backend / frontend
docker compose ps           # 查看健康状态
```

- 前端工作台：http://localhost:3000
- 后端 API：http://localhost:8000　·　健康检查：http://localhost:8000/health
- MinIO 控制台：http://localhost:9001

```bash
docker compose logs -f backend     # 看后端日志
docker compose down                # 停止
```

## 本地开发（不走容器）

```bash
# 后端
cd backend
uv sync                            # 或 pip install -e ".[dev]"
uvicorn app.main:app --reload

# 前端
cd frontend
pnpm install                       # 或 npm install
pnpm dev
```

## 当前进度

M0 地基建设中（见 `tasks/todo.md`）。本提交完成 **T1：脚手架 + 一键启动骨架**。
