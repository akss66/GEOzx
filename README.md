# 同舟行 AI 新媒体运营平台

同舟行是面向新媒体运营团队的 AI Agent 工作台。系统以“运营大脑”为入口，让用户先选择当前平台和账号，再用自然语言提交运营目标，由主 Agent 调度账号定位、内容策略、编导文案、视觉、视频、剪辑、账号运营等专家 Agent，形成可追踪、可审批、可复盘的运营链路。

DyFlow 仅作为内部代号保留，不作为前端主品牌。

## 产品能力

- **运营大脑**：把自然语言目标转成结构化任务，统一承接策略、创作、审批与复盘。
- **多 Agent 协作**：按账号定位、内容策略、文案、视觉、视频和运营等角色拆解工作，并保存每一步状态。
- **账号矩阵**：在平台与账号上下文中组织任务，避免不同品牌、账号和内容资产相互混淆。
- **内容生产链路**：从选题、脚本和素材准备到发布包输出，保留人工确认点和可追踪记录。
- **工具与模型治理**：通过模型网关、工具调用账本和审批门控制外部调用，支持失败恢复与过程审计。

```text
运营目标 → 主 Agent 拆解 → 专家 Agent 协作 → 人工审批 → 发布包 → 数据复盘
               │                    │
               └── 模型与工具网关 ──┴── 调用账本 / 事件流
```

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

## 产品状态与边界

当前版本聚焦可验证的运营闭环，前端与后端均按业务模块持续演进：

- 运营大脑成为默认首页和主工作流入口。
- 顶部显示当前平台和账号，当前阶段优先支持抖音。
- 抖音走官方 OAuth/OpenAPI 路线；审核通过前不做自动发布。
- 发布能力先做发布包准备、人工审批、可复制发布清单和可追踪记录。
- 投流、客服、自动点击发布暂缓，不进入当前验收主线。

## 工程质量

仓库包含后端与前端自动化测试、数据库迁移、容器健康检查和持续集成配置。合并到 `main` 前应至少完成对应模块的单元测试、静态检查与构建验证。
