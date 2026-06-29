# 实现计划：DyFlow 抖音自媒体运营 Agent 工作流系统

> 来源规格：项目根 `SPEC.md`（已评审确认方向）
> 产品/设计基调：`PRODUCT.md`（现代 / 克制 / 精确 / 专业）
> 状态：**待人工评审**　|　创建日期：2026-06-26
> 配套任务清单见 `tasks/todo.md`

---

## 一、概述

DyFlow 是面向企业运营团队的 Web 系统，在系统内编排 8 个 AI Agent，覆盖"账号定位 → 编导文案 → 美术提示词 → 视频创作 → 剪辑 → 运营分发 →（并行）投流 + 客服"全流程，形成数据/事件驱动的内容生产流水线，并叠加规模化执行层（矩阵 / 分发 / 量产 / 素材 / 合规）。

本计划落实 SPEC 第九节的分期路线，从 **M0 地基**开始，按"垂直切片"组织任务——每个切片都能端到端跑通、独立验收。

## 二、规划范围与方法

> **⚠️ 计划调整(2026-06-26)**:M0 后端 T1–T7 已完成并验证。应需求方要求**插入"前端先行"阶段**——先按 PRODUCT.md 设计基调把**完整可演示前端**做出来(指挥台/流水线看板/复盘看板/审批/账号矩阵/知识库/配置/成本),给决策层一个震撼的整体观感;无后端的部分用高仿真 mock 数据撑起。之后再回补后端(T8 项目/账号 CRUD + 真实数据接入 + M1)。设计由 `impeccable` 技能驱动(深色指挥中心、克制专业、WCAG AA、色盲安全)。

- **M0（地基）**：本计划**详细拆解**为 8 个垂直切片任务，每个含验收标准与验证步骤，可直接进入实现。
- **M1（创作闭环，v1 核心）**：**中粒度**列出 epic 与关键任务；进入 M0 检查点后再细化到可实现任务。
- **M2 / M3 / M4**：**epic 级路线**，到达前再做详细分解。

> 原则：依赖自底向上、垂直切片优先、每个任务结束系统仍可运行、每 2–3 个任务一个检查点、高风险任务前置。

## 三、关键架构决策（回顾，详见 SPEC 第一、二、五节）

1. **前后端分离**：FastAPI(async) 后端 + React/TS(Vite + Ant Design) 前端，Monorepo。
2. **多模型网关**：统一 `LLMGateway` 抽象，每个 Agent 绑定首选+兜底模型；**v1 默认全部走 DeepSeek**（已有 Key）。
3. **事件驱动 + 状态机编排**：自研轻量编排引擎 + Redis/arq 事件总线 + 事件溯源（`Event` 表）。
4. **交付物落库 + 版本化**：`Deliverable`（type+version+payload(JSONB)），按 type 用对应 Pydantic schema 校验。
5. **矩阵一等模型**：`Account` 升为一等模型 + `AccountGroup`，所有 Agent 任务可按账号/分组批量下发。
6. **6 道质量门可配置**：脚本合规 / 发布前 / 大额投放(>2000) 强制人工，其余自动通过可打回。
7. **本机 Windows 自托管**：Docker Compose 一键起 `backend + worker + postgres + redis + minio + frontend`，可平滑迁云；对象存储 v1 本地卷，预留 MinIO(S3) 接口。
8. **适配器/插件化集成层**：外部对接（发布/投流/视频生成/数据）统一接口，分期接真实实现，其余先 stub/手动回填。

## 四、M0 依赖图

```
[T1] 仓库脚手架 + docker-compose + .env.example + 前后端可启动骨架
  │
[T2] 后端配置(pydantic-settings) + DB(SQLAlchemy async) + Alembic + 健康检查
  │
[T3] 核心数据模型 + 初始迁移（共享数据契约：Org/User/Role · Project/Account/AccountGroup ·
  │      ContentItem/Deliverable/AgentTask/Event/GateApproval · KnowledgeEntry/ModelConfig/IntegrationConfig）
  │
  ├── [T4] 认证 + RBAC 垂直切片（后端 JWT 登录/me/角色守卫 + 前端登录 + 工作台外壳）
  │
  ├── [T5] 模型网关骨架 + DeepSeek 适配器 + 成本记录 + per-Agent ModelConfig + 测试 ping 端点
  │
  ├── [T6] 事件总线 + arq Worker + 事件溯源（API 发事件 → worker 消费 → 落 Event → WebSocket 推前端）
  │        │
  │        └── [T7] 编排引擎骨架（ContentItem + AgentTask 状态机 + 1 个 Dummy Agent + 1 个质量门节点）
  │
  └── [T8] 前端流水线看板骨架 + 项目/账号 CRUD + WebSocket 实时状态
```

实现顺序：T1 → T2 → T3 →（T4 / T5 / T6 可并行）→ T7（依赖 T6）→ T8（依赖 T4、T7）。

---

## 五、M0 任务详解（垂直切片）

### Task 1：仓库脚手架与一键启动骨架

**描述：** 建立 Monorepo 结构、`docker-compose.yml`、`.env.example`，让 `backend`（FastAPI 空应用 + `/health`）、`frontend`（Vite + React 空壳）、`postgres`、`redis`、`minio` 五个服务能一键拉起并互通。CI（lint + 占位测试）建好。

**验收标准：**
- [ ] `docker compose up -d` 成功拉起 postgres / redis / minio / backend / frontend，无崩溃。
- [ ] 浏览器访问前端首页可见占位工作台；后端 `/health` 返回 200。
- [ ] `.env.example` 含全部所需变量占位（DB / Redis / MinIO / JWT / DeepSeek Key），真实 `.env` 已 `.gitignore`。

**验证：**
- [ ] `docker compose up -d && curl localhost:8000/health` → `{"status":"ok"}`
- [ ] `docker compose ps` 五服务均 healthy；`docker compose down` 干净停止。
- [ ] CI：后端 `ruff check .`、前端 `pnpm lint` 通过。

**依赖：** 无　|　**文件：** `docker-compose.yml`、`.env.example`、`backend/`（pyproject、app/main.py、Dockerfile）、`frontend/`（vite 脚手架、Dockerfile、nginx）、`.github/workflows/ci.yml`　|　**规模：** M

---

### Task 2：后端配置、数据库连接与迁移基座

**描述：** 接入 `pydantic-settings` 读取环境变量；建立 SQLAlchemy 2.x async 引擎/会话依赖、Alembic 迁移；`/health` 升级为探活 DB + Redis 连通性（readiness）。

**验收标准：**
- [ ] `alembic upgrade head` 在空库上成功执行（此时仅有版本表）。
- [ ] `/health` 反映 db 与 redis 实际连通状态（任一不可用返回非 200）。
- [ ] 配置项缺失时启动报清晰错误（fail fast），密钥不写日志。

**验证：**
- [ ] `alembic upgrade head` 退出码 0；`alembic downgrade base` 可回滚。
- [ ] `pytest backend/tests/test_health.py` 通过（mock + 真连各一）。

**依赖：** T1　|　**文件：** `backend/app/config.py`、`backend/app/db.py`、`backend/alembic.ini`、`backend/migrations/env.py`、`backend/app/api/health.py`　|　**规模：** S

---

### Task 3：核心数据模型与初始迁移（共享数据契约）

**描述：** 定义 SPEC 5.4 的核心 ORM 模型与对应基础 Pydantic schema，生成并应用初始迁移。这是事件驱动多 Agent 系统的共享契约，必须先于业务逻辑落地。范围：`Org/User/Role`、`Project/Account/AccountGroup`、`ContentItem/Deliverable`、`AgentTask/Event/GateApproval`、`KnowledgeEntry/ModelConfig/IntegrationConfig`。`Deliverable.payload` 用 JSONB，`type` 决定校验用的 Pydantic schema（建立多态 schema 注册表骨架）。

**验收标准：**
- [ ] 上述模型全部定义，关系/外键/索引/枚举（任务状态、门类型等）正确。
- [ ] `alembic revision --autogenerate` 生成的迁移与模型一致，`upgrade head` 干净应用。
- [ ] `Deliverable` 按 `type` 分派 Pydantic 校验的注册表骨架可用（先注册 1–2 个示例 type）。

**验证：**
- [ ] `pytest backend/tests/test_models.py`：核心模型 CRUD 冒烟 + Deliverable schema 分派校验通过。
- [ ] 迁移可 `downgrade base` 再 `upgrade head` 往返无差异。

**依赖：** T2　|　**文件：** `backend/app/models/*.py`、`backend/app/schemas/*.py`（含 `deliverable.py` 注册表）、`backend/migrations/versions/0001_*.py`、`backend/tests/test_models.py`　|　**规模：** L（如超出可按"身份域 / 业务域 / 编排域"拆 3 个子任务）

---

### Task 4：认证 + RBAC 垂直切片（端到端）

**描述：** 后端 JWT 登录、`/me`、基于 `Role` 的权限守卫（依赖注入式 `require_role`）；前端登录页 + 应用外壳（侧边导航 + 顶栏 + 受角色控制的菜单可见性），登录态持久化与登出。落实 PRODUCT.md 的明暗双主题与 AA 对比度基线。

> **v1 角色模型（已确认）= 两级**：
> - **`admin`（管理员）**：系统配置专属——API Key / per-Agent 模型配置、质量门策略（设定哪些环节强制人工干预）、账号增删改查、集成配置、用户管理。
> - **`user`（企业员工）**：日常使用系统——推进内容流水线、查看/调整 Agent 交付物、看复盘看板、按权限审批质量门。
> 角色枚举保留可扩展性，后续如需细分（编导/审核员/投手/客服…）再加，不改 RBAC 框架。

**验收标准：**
- [ ] 用户可登录获取 JWT，刷新页面保持登录；登出清除令牌。
- [ ] 受保护接口无令牌返回 401，角色不足返回 403。
- [ ] `admin` 可见系统配置菜单（API Key/模型/质量门策略/账号/用户管理）；`user` 仅见日常使用菜单；未授权路由重定向登录。
- [ ] 配置类接口（API Key / 模型配置 / 质量门策略 / 账号 CRUD）仅 `admin` 可调。

**验证：**
- [ ] `pytest backend/tests/test_auth.py`：登录、`/me`、角色守卫 401/403 用例通过。
- [ ] 前端 `pnpm test` 覆盖登录 hook；手动：用两种角色登录看到不同菜单。

**依赖：** T3　|　**文件：** `backend/app/core/auth.py`、`backend/app/api/auth.py`、`frontend/src/pages/Login.tsx`、`frontend/src/components/AppShell.tsx`、`frontend/src/stores/auth.ts`、`frontend/src/api/auth.ts`　|　**规模：** M

---

### Task 5：模型网关骨架 + DeepSeek 适配器 + 成本记录

**描述：** 定义统一 `LLMGateway` 接口（chat/completion、流式预留）、`DeepSeekAdapter` 真实实现、路由（按 `ModelConfig` 选 Agent 模型 + 兜底）、重试/限流骨架、**每次调用记录模型/Token/成本**。提供一个受保护的 `POST /llm/ping` 端点用于联调。

**验收标准：**
- [ ] 经网关用 DeepSeek 完成一次真实 completion（需 Key；CI 中 mock）。
- [ ] 每次调用落一条调用记录（模型、prompt/completion token、成本估算）。
- [ ] per-Agent 选择首选模型，失败可回退兜底（用 mock 验证回退路径）。

**验证：**
- [ ] `pytest backend/tests/test_llm_gateway.py`：路由、兜底、成本记账（全 mock）通过。
- [ ] 手动：配置真实 Key，`POST /llm/ping` 返回 DeepSeek 文本且生成调用记录。

**依赖：** T3　|　**文件：** `backend/app/llm/gateway.py`、`backend/app/llm/adapters/deepseek.py`、`backend/app/llm/cost.py`、`backend/app/api/llm.py`、`backend/tests/test_llm_gateway.py`　|　**规模：** M

---

### Task 6：事件总线 + arq Worker + 事件溯源

**描述：** 建立 Redis/arq Worker、事件发布/订阅封装、`Event` 表事件溯源；前端经 WebSocket 接收实时事件。端到端：API 触发一个 demo 事件 → arq worker 异步消费 → 写 `Event` 行 → 通过 WebSocket 推送到前端。

**验收标准：**
- [ ] API 发布事件后，arq worker 消费并落 `Event`（含类型、payload、时间、关联实体）。
- [ ] 订阅者机制可注册多个 handler；处理失败有重试与失败记录。
- [ ] 前端 WebSocket 实时收到该事件并显示。

**验证：**
- [ ] `pytest -m integration backend/tests/test_events.py`：发布→消费→落库→广播链路（临时 pg+redis）通过。
- [ ] 手动：触发 demo 事件，前端控制台/看板实时出现该事件。

**依赖：** T3　|　**文件：** `backend/app/core/events.py`、`backend/app/worker.py`、`backend/app/api/ws.py`、`backend/tests/test_events.py`、`frontend/src/hooks/useEventStream.ts`　|　**规模：** M

---

### Task 7：编排引擎骨架（状态机 + Dummy Agent + 质量门）

**描述：** 实现链路状态机：`ContentItem` 驱动一组 `AgentTask`（pending/running/done/failed/blocked），由 `Event` 触发推进；接入 1 个 `BaseAgent` 子类 `DummyAgent`（产出经 schema 校验的占位交付物）与 1 个质量门节点（强制人工 → `blocked`，审批后继续）。验证整套"事件→任务→门→审批→继续"骨架，后续 M1 各 Agent 即可挂入。

**验收标准：**
- [ ] 创建 `ContentItem` 后，编排自动推进 Dummy 阶段并产出 `Deliverable`（落库+版本号）。
- [ ] 质量门节点将任务置 `blocked` 并生成 `GateApproval` 待审；审批通过后链路继续。
- [ ] 全过程产生 `Event` 溯源，状态变更经 WebSocket 实时可见。

**验证：**
- [ ] `pytest backend/tests/test_orchestrator.py`：状态机流转、门阻塞/放行、交付物版本化（mock LLM）通过。
- [ ] 手动：建 ContentItem → 看板见 Dummy 完成 → 门待审 → 审批 → 继续。

**依赖：** T6（及 T3、T5）　|　**文件：** `backend/app/agents/base.py`、`backend/app/agents/dummy.py`、`backend/app/orchestrator/engine.py`、`backend/app/orchestrator/gates.py`、`backend/app/api/orchestrator.py`、`backend/tests/test_orchestrator.py`　|　**规模：** L

---

### Task 8：前端流水线看板 + 项目/账号 CRUD + 实时状态

**描述：** 前端工作台核心骨架：项目与账号（含 `AccountGroup` 分组）增删改查页；流水线看板（Kanban）按 `ContentItem`/`AgentTask` 渲染各阶段状态，订阅 WebSocket 实时刷新；待审质量门以醒目方式呈现（落实 PRODUCT.md "人在关键处把关"）。视觉遵循克制基调与 AA 对比度。

**验收标准：**
- [ ] 可创建项目、账号并按分组查看；列表/详情走 TanStack Query。
- [ ] 流水线看板实时反映 T7 编排状态（Dummy 推进、门阻塞），无需手动刷新。
- [ ] 待审质量门在看板/审批区清晰高亮，可一键审批（调 T7 审批接口）。

**验证：**
- [ ] `pnpm test` 覆盖看板与 CRUD hook；`pnpm e2e` 冒烟：建项目→建账号→跑 Dummy 链路→审批门。
- [ ] 手动：两浏览器窗口验证 WebSocket 实时同步。

**依赖：** T4、T7　|　**文件：** `frontend/src/pages/{Projects,Accounts,PipelineBoard,Approvals}.tsx`、`frontend/src/api/*.ts`、`frontend/src/hooks/usePipeline.ts`、`frontend/e2e/m0_smoke.spec.ts`　|　**规模：** L

---

## 六、检查点

### 检查点 A：T1–T3 后（地基就绪）
- [ ] `docker compose up` 五服务健康；`alembic upgrade head` 干净。
- [ ] 核心模型迁移往返无差异，模型测试通过。
- [ ] **与人工确认数据模型契约**后再继续（schema 是后续一切的基础）。

### 检查点 B：M0 完成（T1–T8，端到端骨架跑通）
- [ ] `docker compose up` 一键起全栈；可登录、按角色见菜单。
- [ ] 建项目+账号 → Dummy 链路自动推进 → 质量门阻塞 → 审批 → 继续，看板实时可见。
- [ ] 经网关 DeepSeek 调用成功且成本入账。
- [ ] 后端核心模块测试覆盖达标基线，前端有 M0 冒烟 e2e。
- [ ] **人工评审 M0，确认后进入 M1 详细分解。**

---

## 七、M1 概览（创作闭环 v1 核心，中粒度 — 到检查点 B 后细化）

目标：定位→编导→美术→视频→剪辑→运营 六个 Agent 端到端跑通，默认 DeepSeek，交付物版本化，6 道质量门，共享知识库，富可视化复盘看板，真实接 Seedance + 抖音。

- **E1 Agent 基座与 Prompt 装载**：`BaseAgent` 完善（输入/输出 schema、工具集、模型绑定）；从配置表导出 6 个 system prompt 到 `app/prompts/`。
- **E2 六个创作 Agent**（每个 = 一个垂直切片：prompt + 输出 Pydantic schema + 编排接入 + 测试）：01 定位 / 02 编导 / 03 美术提示词 / 04 视频创作 / 05 剪辑 / 06 运营。
- **E3 主链路编排闭环**：六阶段按事件自动流转，含 6 道质量门接入（Gate3 脚本合规、Gate5 发布前强制人工）。
- **E4 交付物版本化与对比**：交付物历史版本、回滚、上游引用解析。
- **E5 共享知识库**：爆款库 / 用户画像 / 提示词库 / 话术库的读写接口与前端页。
- **E6 富可视化复盘看板**（一等模块）：ECharts 多图（趋势/完播互动/排名/时段热力/平台对比/KPI…），日/周/月切换，报告导出。
- **E7 真实集成 — Seedance**：视频生成适配器接真实 API（已有会员+Key），素材落 MinIO/本地卷。
- **E8 真实集成 — 抖音**：发布 + 数据回流适配器（已有 Key，范围待确认见开放问题）；小红书/视频号/千川/第三方数据先 stub。
- **E9 执行层（借鉴易媒）**：矩阵管理（分组/批量授权）、合规检测服务（敏感词+原创度，接 Gate3）、素材工具集（去/加水印、裁剪、转码、GIF）。
- **E10 闭环反馈**：运营复盘 → 广播 `optimization.suggestion` → 上游订阅响应 → 下次复盘验证，前端"建议→执行→验证"追踪。

## 八、M2 / M3 / M4 路线（Epic，到达前再分解）

- **M2 分发与量产**：分发中心（批量发布 + 定时排期，多平台/多账号）；批量混剪二创去重量产；补齐小红书/视频号发布；抖音评论抓取 + **客服 Agent**（评论回复/负面管理/私域导流/需求反哺）；接入蝉妈妈/飞瓜补充数据。
- **M3 投流自动化**：千川 API + **投流 Agent** 全自动；自动投放规则引擎（关停/追爆/扩量）；ROI 接入复盘看板；Gate6 大额投放强制人工。
- **M4 全自动无人值守 + 优化**：全链路事件/定时触发；告警 + 成本看板；A/B 与内容效果预测模型。

## 九、风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 数据模型（共享契约）后期返工成本高 | 高 | T3 后设检查点 A，人工确认 schema 再继续；Deliverable 用 JSONB + 注册表保留演进弹性 |
| arq/Redis 在 Windows 的兼容与可靠性 | 中 | 选 arq（对 Windows 友好）；T6 用集成测试覆盖发布→消费→重试 |
| 真实外部 API（抖音/Seedance）权限与配额未知 | 高 | M0 全 mock/stub；适配器接口先行，真实接入放 M1 且单独评审；见开放问题 4/5 |
| LLM 输出不稳定导致交付物 schema 校验失败 | 中 | 强制 Pydantic 校验 + 重试/兜底模型；关键字段 golden 断言 + 人工抽检 |
| 数据出境合规（未来接 Claude） | 中 | v1 仅 DeepSeek（境内）；含真实用户数据 Agent 默认国内模型；接境外前单独评审 |
| 单机资源（视频素材体量大） | 中 | 对象存储抽象，v1 本地卷、预留 MinIO；大文件不入 Postgres |
| 范围庞大、一次做完工程量巨大 | 高 | 严格垂直切片 + 分期，每期端到端可跑；M2–M4 到达前才细化 |

## 十、并行化机会

- **可并行**：M0 中 T4 / T5 / T6 互相独立（均只依赖 T3），可并行推进；M1 的 E2 六个 Agent 在 E1 基座与各自输出 schema 契约确定后可并行。
- **须串行**：T1→T2→T3（迁移/共享 schema）；T7 依赖 T6；T8 依赖 T4+T7。
- **需先定契约再并行**：各 Agent 的输出 Pydantic schema 与编排接口，定稿后并行实现。

## 十一、开放问题（需人工输入）

> 沿用 SPEC 第十一节。**已确认**：① 角色模型 = `admin` + `user` 两级（见 Task 4）；② 对象存储 = 本地磁盘卷起步 + MinIO 接口预留。

1. ~~团队角色定义~~ → **已确认：`admin`（系统配置/账号CRUD/质量门策略/用户管理）+ `user`（日常使用），枚举可扩展。**
2. **抖音 API 范围**（影响 M1 E8 真实接入边界）——*已调研，待你确认应用已通过的接口权限*：
   - 抖音开放平台能力分模块申请：**内容能力(视频上传/发布)**、**数据分析(视频/粉丝数据)** → M1 必需；**互动管理(评论列表/回复, scope `item.comment`)**、**私信** → M2 客服；**千川投流属巨量引擎 marketing API 另一套体系** → M3。
   - ⚠️ 关键：AppID/Key 本身不等于能力，**每个接口需在应用后台「接口权限」单独申请、审核通过**，OAuth 授权带对应 scope 才能调。
   - **需你确认**：现有抖音应用**已申请通过了哪些接口权限**？(至少需"视频发布 + 数据分析"才能跑通 M1 E8 真实发布+回流；否则 E8 先 stub/手动回填，待权限到位再切真实。)
3. **数据模型契约确认**（检查点 A）：T3 完成后就 SPEC 5.4 模型集做一次确认。
4. **小红书/视频号权限**（M2）：账号与开放平台权限是否具备？
5. ~~对象存储~~ → **已确认：本地磁盘卷起步，抽象接口预留 MinIO，迁云零成本改动。**
6. **复盘数据来源**：抖音官方数据是否足以支撑看板全部指标，不足是否需第三方补充？
7. **系统正式命名**：暂用 "DyFlow"，是否定名？
8. **内网穿透方案**：frp / cloudflared / 仅局域网？
9. **多租户**：当前单团队单部署，未来是否要对外多租户 SaaS（影响数据隔离设计）？
