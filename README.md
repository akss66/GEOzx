# 同舟行 AI 新媒体运营平台

同舟行是面向新媒体运营团队的 AI Agent 工作台。系统以“运营大脑”为入口，让用户先选择当前平台和账号，再用自然语言提交运营目标，由主 Agent 调度账号定位、内容策略、编导文案、视觉、视频、剪辑、账号运营等专家 Agent，形成可追踪、可审批、可复盘的运营链路。

DyFlow 仅作为内部代号保留，不作为前端主品牌。

- 产品方向：[`PRODUCT.md`](PRODUCT.md)
- 系统规格：[`SPEC.md`](SPEC.md)
- 设计系统：[`DESIGN.md`](DESIGN.md)
- 当前执行清单：[`tasks/current.md`](tasks/current.md)
- 归档计划：[`docs/archive/2026-07-doc-reset/`](docs/archive/2026-07-doc-reset/)

## 技术栈

- 后端：Python 3.11 + FastAPI(async) + PostgreSQL 16 + Redis 7 + arq + SQLAlchemy/Alembic
- 前端：React 18 + TypeScript + Vite + Ant Design
- 编排：自研轻量状态机 + 事件驱动；多模型网关；工具调用账本；人工审批门
- 部署：Docker Compose 单机部署，可迁移到公网服务器

## 快速开始

前置：Docker Desktop 或服务器 Docker 环境。

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

当前处于模块化重构阶段：前端和后端同步推进，先把运营大脑、账号矩阵、内容生产、人工审批、运营复盘等模块做成真实可验收链路。

当前重点：

- 运营大脑成为默认首页和主工作流入口。
- 顶部显示当前平台和账号，当前阶段优先支持抖音。
- 抖音走官方 OAuth/OpenAPI 路线；审核通过前不做自动发布。
- 发布能力先做发布包准备、人工审批、可复制发布清单和可追踪记录。
- 投流、客服、自动点击发布暂缓，不进入当前验收主线。

旧的 `tasks/plan.md`、`tasks/todo.md`、前端重构计划和系统审查文档已归档到 `docs/archive/2026-07-doc-reset/`，不再作为当前事实来源。
