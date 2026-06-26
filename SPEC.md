# Spec: 抖音自媒体运营 Agent 工作流系统（代号：DyFlow）

> 来源需求：`需求文档/抖音自媒体运营Agent团队配置表.xlsx`
> 本文档为系统设计规格（Specification），是开发前的唯一事实来源。经确认后再进入 PLAN / TASKS / IMPLEMENT 阶段。
> 状态：**待评审**　|　创建日期：2026-06-26

---

## 一、目标 Objective

### 我们要做什么
构建一个面向**企业运营团队**的 Web 系统，在系统内实现并**编排** 8 个 AI Agent，覆盖抖音自媒体运营全流程：

> 账号定位 → 编导文案 → 美术指导（提示词）→ 视频创作 → 剪辑 → 账号运营 →（并行）投流 + 客服

系统不只是"8 个聊天机器人"，而是一个**数据驱动、事件驱动的内容生产流水线**：每个 Agent 的交付物落库并版本化，上游产出自动触发下游任务，运营专家的复盘优化建议作为反馈事件广播回全体 Agent，形成"数据 → 洞察 → 优化 → 执行"的闭环。

### 谁来用
一个**企业运营团队**（多人协作，需账号体系与角色权限）。最终目标是全自动无人值守运营，关键风险环节由人工质量门把关。

### 成功是什么样
- 团队在一个 Web 工作台里就能驱动从"账号定位"到"成片发布 + 投流 + 评论运营"的完整链路；
- Agent 之间无需人工搬运交付物，系统自动流转；
- 运营复盘后，优化建议自动分发并被上游 Agent 在下一周期响应、可追踪效果；
- 关键质量门（合规/发布/大额投放）由人确认，其余自动通过且可一键打回；
- 多模型可按 Agent 配置，按质量/成本灵活分配，不绑定单一供应商；
- 全程可在**本机（Windows）单机部署**跑通，后期可平滑迁移到云。

### 核心设计决策（已与需求方确认）
| # | 决策点 | 结论 |
|---|---|---|
| 1 | 形态 / 用户 | **Web 应用**，使用者为运营团队（多人协作） |
| 2 | 自动化程度 | **全自动（C）**；企业资质齐全；外部对接用**适配器 + 插件化集成层**，预留扩展，分期接入 |
| 3 | 技术栈 | **Python（FastAPI）后端 + React/TypeScript 前端**，前后端分离 |
| 4 | 大模型 | **多模型可切换**：统一模型网关，每个 Agent 可独立配置模型 |
| 5 | 数据 / 协作 | **PostgreSQL 落库 + 事件驱动流转 + 交付物版本化 + 共享知识库 + 反馈闭环** |
| 6 | 质量门 | **可配置人工审核**；脚本合规 / 发布前 / 大额投放（日耗>2000）**强制人工**，其余自动通过、可一键打回 |
| 7 | 部署 | **本机 Windows 自托管**，Docker Compose 单机一键起，可平滑迁云 |
| 8 | 分期 | 由设计方定（见"九、分期路线图"） |

---

## 二、技术栈 Tech Stack

### 后端
- **语言 / 框架**：Python 3.11+，FastAPI（async）
- **数据库**：PostgreSQL 16（业务数据 / 交付物 / 版本 / 任务 / 审批 / 知识库）
- **ORM / 迁移**：SQLAlchemy 2.x（async）+ Alembic
- **缓存 / 事件总线 / 队列**：Redis 7 + **arq**（asyncio 原生任务队列，跨平台、对 Windows 友好；不选 Celery 以规避 Windows 兼容坑）
- **对象存储**：v1 用本地磁盘卷；预留 **MinIO**（S3 兼容）接口，迁云即换 OSS/COS，不改业务代码
- **数据校验 / Schema**：Pydantic v2（交付物 payload 统一用 Pydantic 模型校验）
- **Agent 编排**：自研轻量状态机 + 事件驱动（核心可控）；复杂分支预留 LangGraph 适配位
- **实时推送**：WebSocket（看板状态、任务进度实时更新）
- **认证**：JWT + RBAC（角色权限）

### 前端
- **框架**：React 18 + TypeScript + Vite
- **UI**：Ant Design（中后台组件齐全，契合运营工作台）
- **状态 / 数据**：TanStack Query（服务端状态）+ Zustand（本地状态）
- **图表**：ECharts（复盘仪表盘）
- **实时**：原生 WebSocket / socket 客户端

### 大模型网关（多模型抽象）
统一 `LLMGateway` 接口，适配：
- 国内模型：DeepSeek（**当前已有 Key，v1 默认模型**）、豆包（Doubao）、通义千问、Kimi
- Anthropic Claude（Opus / Sonnet）—— 取得授权且确认合规后接入
- 每个 Agent 在配置中绑定首选模型 + 兜底模型；网关负责路由、重试、限流、Token/成本统计
- **v1 全部 Agent 默认走 DeepSeek**；架构保持多模型，后续有 Key 再按 Agent 切换更优模型

> ⚠️ 合规：境内企业数据发往境外模型（Claude）需评估数据出境合规。系统按 Agent 配置模型，**默认创作类可选高质量模型、含真实用户/客户数据的 Agent（如客服、投流人群）优先国内模型**。具体策略见"边界"与"开放问题"。

### 外部集成（适配器层，插件化）
统一抽象接口，按现有资产分期接真实实现，其余先 stub / 手动回填：
- **AI 视频生成：Seedance（已有会员 + API Key，M1 先接真实）**、可灵、Runway 等预留
- **平台发布 / 数据：抖音开放平台（已有 API Key，M1 接发布 + 数据）**；小红书 / 视频号预留
- 投流：巨量千川（预留，M3 接入）
- 数据抓取：蝉妈妈 / 飞瓜（预留；优先用平台官方数据，第三方为补充）
- 评论 / 私信：抖音评论 API（客服，M2）

> **当前可用凭证**：DeepSeek API Key · Seedance 会员 + API Key · 抖音 API Key。这三样决定 M1 的真实集成范围。

### 部署 / 运维
- **Docker Compose** 一键编排：`backend` + `worker(arq)` + `postgres` + `redis` + `minio` + `frontend(nginx)`
- 本机 Windows 11 Pro 自托管（Docker Desktop）
- 团队访问：局域网 / 内网穿透（如 frp、cloudflared）
- 迁云：同一套 compose 上云即可，存储/凭证走环境变量

---

## 三、命令 Commands

> 以下为目标命令规划（脚手架建立后落地为实际脚本）。

```bash
# —— 一键启动（生产/演示）——
docker compose up -d                      # 拉起全部服务
docker compose logs -f backend worker     # 看后端与 worker 日志
docker compose down                       # 停止

# —— 后端开发 ——
cd backend
uv sync                                   # 安装依赖（用 uv 管理）
uvicorn app.main:app --reload             # 启动 API（开发）
arq app.worker.WorkerSettings             # 启动任务/事件 worker
alembic upgrade head                      # 应用数据库迁移
alembic revision --autogenerate -m "msg"  # 生成迁移（改 schema 后）

# —— 后端质量 ——
pytest                                     # 全部测试
pytest -m "not integration"               # 仅单元测试
pytest --cov=app --cov-report=term-missing # 覆盖率
ruff check . --fix                         # Lint + 自动修复
ruff format .                              # 格式化
mypy app                                   # 类型检查

# —— 前端开发 ——
cd frontend
pnpm install
pnpm dev                                   # Vite 开发服务器
pnpm build                                 # 生产构建
pnpm test                                  # vitest 单测
pnpm lint                                  # eslint
pnpm e2e                                   # Playwright 端到端
```

---

## 四、项目结构 Project Structure

Monorepo：

```
DyFlow/
├─ SPEC.md                      # 本文档
├─ docker-compose.yml           # 单机一键编排
├─ .env.example                 # 环境变量样例（凭证/模型 key 不入库）
│
├─ backend/                     # FastAPI 后端
│  ├─ app/
│  │  ├─ main.py                # 应用入口
│  │  ├─ config.py              # 配置（pydantic-settings）
│  │  ├─ api/                   # REST + WebSocket 路由
│  │  ├─ core/                  # 鉴权 / 权限 / 事件总线 / 异常
│  │  ├─ models/                # SQLAlchemy ORM 模型
│  │  ├─ schemas/               # Pydantic schema（含各交付物 payload schema）
│  │  ├─ agents/                # 8 个 Agent 运行时
│  │  │  ├─ base.py             # Agent 基类（输入/输出/工具/模型绑定）
│  │  │  ├─ positioning.py      # 01 账号定位
│  │  │  ├─ content_director.py # 02 编导文案
│  │  │  ├─ art_director.py     # 03 美术指导（提示词）
│  │  │  ├─ video_creator.py    # 04 视频创作
│  │  │  ├─ editor.py           # 05 剪辑
│  │  │  ├─ operator.py         # 06 账号运营（数据中枢）
│  │  │  ├─ advertiser.py       # 07 投流
│  │  │  └─ customer_service.py # 08 客服
│  │  ├─ prompts/               # 各 Agent 的 system prompt（md，源自配置表）
│  │  ├─ orchestrator/          # 编排引擎：链路状态机 + 事件流转 + 质量门
│  │  ├─ llm/                   # 模型网关（多模型适配 + 路由 + 成本统计）
│  │  ├─ integrations/          # 外部集成适配器（插件化）
│  │  │  ├─ base.py             # 统一接口定义
│  │  │  ├─ publish/            # 平台发布（抖音/小红书/视频号）
│  │  │  ├─ ads/                # 千川投流
│  │  │  ├─ video_gen/          # AI 视频生成（可灵/Seedance/…）
│  │  │  └─ analytics/          # 数据抓取（蝉妈妈/飞瓜/平台）
│  │  ├─ knowledge/             # 共享知识库（爆款库/画像/提示词库/话术库）
│  │  ├─ execution/             # 规模化执行层（借鉴易媒）
│  │  │  ├─ matrix/             # 多账号矩阵管理（分组/批量授权）
│  │  │  ├─ distribution/       # 分发中心（批量发布 + 定时排期）
│  │  │  ├─ material/           # 素材工具集（去/加水印/裁剪/转码/GIF）+ 批量混剪量产
│  │  │  └─ compliance/         # 合规检测服务（敏感词/原创度/限流风险）
│  │  └─ worker.py              # arq worker（消费事件/任务）
│  ├─ migrations/               # Alembic
│  └─ tests/                    # 后端测试（unit / integration）
│
├─ frontend/                    # React 前端
│  └─ src/
│     ├─ pages/                 # 工作台 / 流水线看板 / 审批 / 复盘 / 知识库 / 配置
│     ├─ components/
│     ├─ api/                   # 后端接口封装
│     ├─ hooks/
│     └─ stores/
│
├─ infra/                       # 部署相关（nginx、初始化脚本等）
└─ docs/                        # ADR、集成对接说明、运维手册
```

---

## 五、系统架构 Architecture

### 5.1 分层
```
┌────────────────────────────────────────────────────────────┐
│  前端 React 工作台：流水线看板 / 交付物 / 质量门审批 / 复盘 / 知识库 / 配置 │
└───────────────▲───────────────────────────▲────────────────┘
            REST/WebSocket                实时推送
┌───────────────┴───────────────────────────┴────────────────┐
│  FastAPI：API 层 + 鉴权/RBAC                                   │
├──────────────────────────────────────────────────────────────┤
│  编排引擎 Orchestrator：链路状态机 · 事件总线 · 质量门 · 闭环反馈  │
├───────────────┬───────────────┬──────────────┬───────────────┤
│  Agent 运行时   │  模型网关 LLM   │  集成适配器     │  共享知识库      │
│  (8 个 Agent)  │ (多模型路由)    │ (发布/投流/视频/数据) │ (爆款/画像/提示词/话术) │
├───────────────┴───────────────┴──────────────┴───────────────┤
│  arq Worker（消费任务/事件）                                     │
├──────────────────────────────────────────────────────────────┤
│  PostgreSQL（业务/交付物/版本/任务/审批） · Redis（事件/队列） · 对象存储 │
└──────────────────────────────────────────────────────────────┘
```

### 5.2 八个 Agent（system prompt 源自配置表对应 Sheet）
| 编号 | Agent | 核心职责 | 主要交付物 |
|---|---|---|---|
| 01 | 账号定位专家 | 定位策略 / 对标拆解 / 动态监控 | 定位策略文档、对标数据库、差异化建议、定位优化报告 |
| 02 | 编导文案专家 | 选题策划 / 发布规划 / 脚本创作 | 选题方案、发布日历、视频脚本、分镜建议、配乐建议、制作 Brief |
| 03 | 美术指导提示词专家 | 视觉风格 / 结构化 AI 提示词 | 视觉风格书、AI 提示词脚本、提示词版本表、美学标准 |
| 04 | 视频创作专家 | AI 视频生成执行 / 素材管理 | 生成工具计划、素材交付清单、素材版本表、质量反馈 |
| 05 | 剪辑专家 | 后期加工 / 爆款风格 / 多平台适配 | 工程文件、成片交付清单、剪辑说明、风格库 |
| 06 | 账号运营专家 | 多平台分发 / 数据复盘 / **优化分发（数据中枢）** | 日/周/月复盘、发布记录、数据看板、优化建议（广播全员） |
| 07 | 投流专家 | 千川投放 / 人群定向 / ROI / 自动投放 | 投放计划、投放日/周报、人群定向效果分析 |
| 08 | 客服专家 | 评论回复 / 负面管理 / 私域导流 / 需求反哺 | 评论日报、用户需求报告（反哺编导）、高意向用户表、话术库 |

每个 Agent = `system prompt（角色/能力/流程/输出标准/协作接口）+ 绑定模型 + 工具集（集成适配器）+ 输入/输出 Pydantic schema`。

### 5.3 编排与闭环（事件驱动）
- **主链路（线性）**：`定位.done → 触发编导 → 编导.done → 触发美术 → 美术.done → 触发视频 → 视频.done → 触发剪辑 → 剪辑.done → 触发运营发布`
- **并行链路**：投流（与运营并行，基于内容数据投放）；客服（贯穿全生命周期）
- **闭环反馈**：`运营.复盘完成 → 广播 optimization.suggestion 事件 → 定位/编导/美术/剪辑/投流/客服 订阅 → 各自在下一周期响应 → 运营下次复盘验证效果`
- **需求反哺**：`客服.用户需求报告 → 编导选题输入`
- **质量门**：链路中的 Gate 节点拦截流转，等待审批/自动通过（见 5.5）

### 5.4 核心数据模型（PostgreSQL）
- `Org` / `User` / `Role`（多用户、RBAC）
- `Project`（运营项目，绑定账号/定位）、`Account`（抖音/小红书/视频号账号，**矩阵一等模型**）、`AccountGroup`（账号分组：赛道/人设/平台）
- `ContentItem`（一条内容贯穿全链路的实例，含当前阶段、状态）
- `Deliverable`（交付物：`type` + `version` + `agent` + `status` + `payload(JSONB)`；多态：定位策略/选题/脚本/提示词/素材/成片/复盘/投流计划/客服记录…，按 type 用对应 Pydantic schema 校验）
- `DistributionTask` / `PublishSchedule`（分发中心：一内容→多平台/多账号的定时排期与发布状态）
- `MaterialAsset`（素材库：原始/混剪/成片，含工具处理记录）、量产任务 `BatchRemixJob`
- `ComplianceCheck`（合规检测记录：敏感词/原创度/限流风险，关联 Gate3 与发布前）
- `AgentTask`（任务状态机：pending / running / done / failed / blocked）
- `Event`（事件日志 / 事件溯源）
- `GateApproval`（质量门审批记录：门类型、决策、审批人、意见）
- `KnowledgeEntry`（共享知识库：爆款库 / 用户画像 / 提示词库 / 话术库，可读可写）
- `ModelConfig`（per-Agent 模型配置 + 兜底）
- `IntegrationConfig`（集成凭证加密存储 + 开关）
- `MetricSnapshot`（各平台数据指标快照）、`AdPlan` / `AdSpend`（投流）

### 5.5 质量门（可配置，6 道）
| Gate | 节点 | v1 默认 |
|---|---|---|
| 1 定位审核 | 定位策略完成后 | 自动通过（可打回） |
| 2 选题审核 | 月度选题完成后 | 自动通过（可打回） |
| 3 脚本合规 | 脚本完成后（违禁词/侵权） | **强制人工** |
| 4 成片审核 | 剪辑完成后 | 自动通过（可打回） |
| 5 发布前审核 | 发布前（标题/标签/封面/导流合规） | **强制人工** |
| 6 大额投放 | 日耗>2000 的投放计划 | **强制人工** |

每道门可在后台开/关与改阈值；强制门未通过则链路阻塞（`AgentTask=blocked`）。

### 5.6 数据复盘可视化看板（一等公民模块）
复盘是运营专家的核心职责，也是整个闭环的数据源。系统提供**富可视化看板**（ECharts），支持日/周/月维度切换、多平台对比、按项目/账号/内容筛选，并可导出 PNG/PDF 报告。规划图表：

| 主题 | 图表类型 | 关键指标 |
|---|---|---|
| 流量趋势 | 折线 / 面积图 | 播放量、曝光量、流量来源构成 |
| 完播 & 互动 | 折线 + 阈值线 | 完播率(>30%)、点赞率、评论率、转发率、收藏率 |
| 粉丝增长 | 折线 + 取关率 | 新增/累计粉丝、净增曲线、取关率 |
| 内容排名 | 横向条形 / TOP-BOTTOM 对比 | 本周 TOP3 / BOTTOM3 内容对比 |
| 内容类型表现 | 雷达 / 分组柱状 | 各内容类型完播/互动/转化对比 |
| 发布时段效果 | 热力图 | 24h × 7天 发布时段 × 效果 |
| 平台对比 | 分组柱状 / 堆叠 | 抖音 vs 小红书 vs 视频号 各核心指标 |
| KPI 达成 | 仪表盘 / 进度环 | 月度 KPI 达成率 |
| 投流 ROI | 折线 + 保本线 | ROI 趋势、消耗、CPM、CTR、转化率 |
| 人群定向效果 | 桑基 / 分组柱状 | 各定向组合的花费/转化/ROI |
| 评论情感 | 饼图 / 堆叠条 | 正面/中性/负面占比、TOP 关键词词云 |
| 私域转化 | 漏斗图 | 曝光 → 互动 → 高意向 → 进群/加微 |
| 闭环追踪 | 时间线 / 桑基 | 优化建议 → 执行 → 效果验证 链路 |

所有指标来自 `MetricSnapshot` / `AdSpend` / 客服评论数据；运营专家的复盘报告交付物可直接引用看板图表。

### 5.7 规模化执行层（借鉴易媒助手，垫在 Agent 智能层之下）
DyFlow 的差异化 = AI Agent 的"创作 + 决策 + 优化大脑"；但矩阵运营还需要一层"规模化执行 + 效率工具"。借鉴易媒助手补齐以下 5 项能力，与 8 个 Agent 协同（Agent 产出策略/内容，执行层负责规模化落地）：

1. **多账号矩阵管理**：账号分组（按赛道/人设/平台）、批量授权与 Token 托管、规模化运维上千账号；`Account` 升级为一等矩阵模型，新增 `AccountGroup`。所有 Agent 任务可按账号/分组维度批量下发。
2. **分发中心（批量发布 + 定时分发）**：一条内容 → 多平台自动适配（比例/封面/标题/标签）→ 定时排期队列批量发布；统一发布记录与状态回流。建立在平台发布适配器之上。
3. **批量混剪二创去重量产**（矩阵铺号核心场景）：剪辑 Agent 增加"批量量产模式"——基于素材自动混剪、批量二创去重，一次产出大量差异化短视频供矩阵号铺量（仍受 30% 差异化与合规约束）。
4. **素材工具集**：去水印 / 加水印 / 裁剪 / 转码 / GIF 导出等高频工具，挂在剪辑/素材模块，供量产与适配复用。
5. **合规检测服务**：独立服务 = 敏感词库检测 + 原创度/查重 + 限流风险评估；作为 **Gate 3（脚本合规）** 与发布前的强制依赖，发布/投放链路统一调用。

> 定位：执行层让 Agent 的高质量产出能**规模化、合规化、自动化**地落到上千矩阵账号。带货挂载（小黄车/团购）暂不做。

---

## 六、代码风格 Code Style

### Python（后端）
- `ruff` 负责 lint + format；强制 type hints；async 优先；业务对象用 Pydantic。
- 命名：模块/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE`。

```python
# backend/app/agents/base.py
from abc import ABC, abstractmethod
from pydantic import BaseModel

from app.llm.gateway import LLMGateway
from app.schemas.deliverable import DeliverablePayload


class AgentContext(BaseModel):
    content_item_id: int
    upstream: dict[str, DeliverablePayload]   # 上游交付物（按 type 取）
    knowledge: dict[str, list[dict]]          # 共享知识库切片


class BaseAgent(ABC):
    """所有 Agent 的基类：绑定 system prompt、模型、输入/输出 schema。"""

    code: str                 # 如 "01-positioning"
    system_prompt_path: str   # app/prompts/01_positioning.md
    output_schema: type[BaseModel]

    def __init__(self, llm: LLMGateway) -> None:
        self._llm = llm

    @abstractmethod
    async def run(self, ctx: AgentContext) -> DeliverablePayload:
        """执行一次工作，产出经 schema 校验的交付物。"""
        ...
```

### TypeScript（前端）
- `eslint` + `prettier`；函数式组件 + hooks；服务端状态走 TanStack Query，禁止在组件里散落 fetch。
- 命名：组件 `PascalCase`，hook `useXxx`，变量/函数 `camelCase`。

```tsx
// frontend/src/pages/PipelineBoard.tsx
import { usePipeline } from "@/hooks/usePipeline";

export function PipelineBoard({ projectId }: { projectId: number }) {
  const { stages, isLoading } = usePipeline(projectId);
  if (isLoading) return <Spin />;
  return <Kanban stages={stages} />;
}
```

---

## 七、测试策略 Testing Strategy

- **后端单元测试**（pytest）：Agent 输出 schema 校验、编排状态机流转、质量门逻辑、模型网关路由/兜底、集成适配器接口契约。**LLM 与外部集成全部 mock**。
- **后端集成测试**（pytest + httpx，标记 `@pytest.mark.integration`）：API 端到端、DB 落库/版本化、事件流转触发下游、闭环反馈广播。用临时 Postgres + Redis（compose 测试栈）。
- **Agent 质量测试**：交付物 schema 强校验（结构正确）+ 关键字段断言（golden）；可选 LLM-as-judge 对脚本/提示词做质量打分（人工抽检兜底）。
- **前端**：vitest + React Testing Library（组件/hook）；Playwright 跑核心流程 e2e（建项目 → 跑链路 → 审批 → 看复盘）。
- **覆盖率期望**：后端核心模块（orchestrator / agents / llm / integrations 接口）行覆盖 ≥ 80%；前端关键页面有冒烟 e2e。
- **测试位置**：后端 `backend/tests/`，前端 `frontend/src/**/*.test.tsx` + `frontend/e2e/`。

---

## 八、边界 Boundaries

### 始终要做 Always
- 交付物入库前必须通过对应 Pydantic schema 校验；
- 外部凭证/模型 Key 加密存储，绝不入代码库（走 `.env` / 密钥管理）；
- 敏感操作（发布、投放、隐藏/删除评论）写审计日志；
- 提交前跑测试 + lint；
- 脚本/文案过违禁词与侵权检查；
- 记录每次 LLM 调用的模型、Token、成本。

### 先问再做 Ask First
- 新增依赖；
- 改数据库 schema（生成迁移）；
- 接入 / 改动任一外部真实 API（发布、千川、视频生成、数据抓取）；
- 改动 Agent 编排链路或质量门默认策略；
- 改动 Agent 的核心 system prompt（源自配置表）；
- 放开/关闭强制质量门；
- 配置真实投放预算/出价；
- 让含真实用户/客户数据的 Agent 使用境外模型（数据出境合规）。

### 绝不要做 Never
- 提交任何密钥/凭证/Cookie 到仓库；
- 绕过强制质量门直接发布或投放；
- 未授权搬运/二次发布他人内容（二创差异化 <30% 视为搬运）；
- 删除失败的测试来"让 CI 变绿"；
- 在未确认合规前把企业敏感数据发往未授权的境外模型；
- 在评论区与用户争论 / 违反平台规则的导流（用合规话术与替代策略）。

---

## 九、分期路线图（设计方建议，待确认）

> 全自动是目标，但所有真实集成一次做完工程量巨大。建议按"垂直切片"分期，每期都能端到端跑通。

- **M0 地基**：脚手架（compose/CI）、认证 + RBAC、核心数据模型（含 `Account`/`AccountGroup` 矩阵模型）、模型网关骨架、事件总线 + arq、编排引擎骨架、前端工作台骨架。
- **M1 创作闭环（v1 核心）**：定位 → 编导 → 美术 → 视频 → 剪辑 → 运营 六个 Agent 跑通；多模型编排（**默认 DeepSeek**）；交付物落库 + 版本化；6 道质量门；共享知识库；**富可视化复盘看板**。
  - 外部集成（用现有凭证）：**AI 视频生成接 Seedance（真实）**；**抖音接发布 + 数据回流（真实）**；小红书/视频号、千川、第三方数据抓取**先做适配器接口 + 手动回填**。
  - 执行层（借鉴易媒）：**矩阵管理（账号分组/批量授权）**、**合规检测服务（敏感词+原创度，接 Gate3）**、**素材工具集（去/加水印、裁剪、转码、GIF）** 上线。
- **M2 分发与量产**：**分发中心（一键批量发布 + 定时排期，多平台/多账号）**；**批量混剪二创去重量产模式**；补齐小红书/视频号发布；抖音评论抓取 + **客服 Agent** 上线（评论回复/负面管理/私域导流/需求反哺）；接入第三方数据抓取（蝉妈妈/飞瓜，作为官方数据补充）。
- **M3 投流自动化**：千川 API + **投流 Agent** 全自动；自动投放规则引擎（关停/追爆/扩量）；ROI 监控接入复盘看板。
- **M4 全自动无人值守 + 优化**：全链路事件/定时触发；告警 + 成本看板；A/B 与"内容效果预测模型"。

---

## 十、成功标准 Success Criteria（可验证）

- [ ] 团队成员可登录系统、按角色权限访问对应功能；
- [ ] 可创建一个运营项目并绑定账号信息；
- [ ] 从"账号定位"触发，系统**自动**沿主链路流转到"运营"，每步产出经 schema 校验的交付物并落库 + 版本化（M1 端到端跑通）；
- [ ] 强制质量门（脚本合规/发布前/大额投放）会阻塞链路并在前端出现待审批，审批通过后继续；其余门自动通过且可一键打回；
- [ ] 运营专家完成复盘后，优化建议作为事件广播到相关 Agent，前端可见"建议 → 执行 → 验证"的追踪；
- [ ] **复盘看板提供富可视化图表**（趋势/对比/排名/时段热力/平台对比/ROI/情感/漏斗等），支持日/周/月切换与报告导出；
- [ ] 每个 Agent 的模型可在配置页独立切换（v1 默认 DeepSeek），调用成本可在看板查看；
- [ ] 外部集成均通过统一适配器接口接入，新增/替换实现不改动编排主流程；
- [ ] 支持账号分组的**矩阵管理**，Agent 任务可按账号/分组批量下发；
- [ ] **合规检测服务**（敏感词+原创度）接入 Gate3，不通过则脚本链路阻塞；
- [ ] **分发中心**可一条内容定时批量发布到多平台/多账号并回流状态（M2）；
- [ ] `docker compose up` 可在本机 Windows 一键拉起全部服务并访问工作台；
- [ ] 后端核心模块测试覆盖 ≥ 80%，核心流程有 e2e。

---

## 十一、开放问题 Open Questions

> ✅ 已确认：方向整体通过；v1 默认模型 = DeepSeek；M1 视频生成接 Seedance、平台接抖音；复盘需富可视化图表。
> 当前可用凭证：**DeepSeek API Key · Seedance 会员 + API Key · 抖音 API Key**。

1. **系统名称 / 代号**：暂用 "DyFlow"，是否需要正式命名？
2. **后续模型扩展**：豆包/通义/Kimi/Claude 之后接哪几家？哪些 Agent 用哪个模型最优（成本/质量）由系统配置，先用 DeepSeek 跑通即可。
3. **数据出境合规**：未来接 Claude 时，哪些 Agent 允许用境外模型？含真实用户数据的客服/投流是否限定国内模型？
4. **抖音 API 范围**：现有 Key 覆盖哪些能力（内容发布 / 数据查询 / 评论管理 / 千川）？需确认权限以定 M1 真实接入边界。
5. **小红书 / 视频号**：账号与开放平台权限是否具备？M2 接入。
6. **多租户**：当前"单团队单部署"，未来是否要做对外多租户 SaaS？（影响数据隔离设计）
7. **团队角色定义**：需要哪些角色（管理员/运营/编导/审核员/投手/客服…）及权限边界？
8. **内网穿透方案**：团队远程访问用 frp / cloudflared / 还是仅局域网？
9. **对象存储**：v1 本地磁盘是否够用？视频素材体量大，是否一开始就上 MinIO？
10. **复盘数据来源**：抖音官方数据是否足够支撑看板全部指标？不足部分是否需第三方（蝉妈妈/飞瓜）补充？
```

