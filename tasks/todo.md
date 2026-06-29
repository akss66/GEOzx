# DyFlow 任务清单 TODO

> 配套计划：`tasks/plan.md`。勾选规则：任务下全部验收+验证项达成才勾顶层。
> 当前阶段：**M0 地基**（详细任务）。M1 中粒度、M2–M4 epic，到检查点后细化。
> 状态图例：⬜ 未开始　🟦 进行中　✅ 完成　⛔ 阻塞

---

## Phase M0 — 地基

- [ ] **T1 仓库脚手架 + 一键启动骨架**（M，依赖：无）
  - [x] Monorepo 结构 + `docker-compose.yml`（pg/redis/minio/backend/frontend）
  - [x] `.env.example` 全量占位；真实 `.env` 入 `.gitignore`（+ `.gitattributes` 统一 LF）
  - [x] backend FastAPI 空应用 + `/health`；frontend Vite+React 空壳
  - [x] CI：`ruff check` + `pnpm lint` + 占位测试
  - [x] 本地验证：后端 ruff+format+pytest(2 passed)；前端 eslint+tsc+vite build 全绿；`pnpm install/lint/build` 退出码 0
  - [x] ✅ Docker 验证通过：`docker compose up -d --build` 五服务全 healthy；`/health`、前端 :3000、nginx 反代 `/api/health`、MinIO 健康端点均 200
    - 跑通修复：① 前端 Dockerfile 补 `COPY pnpm-workspace.yaml`(容器内 esbuild 构建放行)；② 前端健康检查 `localhost`→`127.0.0.1`(避开 IPv6 ::1);③ Docker 配国内镜像加速(daocloud 等)

- [ ] **T2 配置 + DB + 迁移基座**（S，依赖：T1）
  - [x] `pydantic-settings` 配置；SQLAlchemy async 引擎/会话依赖（`app/db.py`：engine/session/Base + check_db/check_redis）
  - [x] Alembic 初始化（async env.py + 0001 基线迁移）；`/health` 拆为存活 `/health` + 就绪 `/health/ready`(DB+Redis)
  - [x] 验证：容器内 `alembic upgrade head`→`downgrade base`→`upgrade head` 往返成功；`/health/ready` 200(db+redis true)；backend 健康检查改走 `/health/ready` 仍 healthy
  - [x] 单测 4 passed（存活/root + 就绪 ready/degraded，monkeypatch 不依赖真实 DB）；ruff 通过

- [ ] **T3 核心数据模型 + 初始迁移**（L，依赖：T2）⚠️ 共享契约
  - [x] ORM：Org/User · Project/AccountGroup/Account · ContentItem/Deliverable · AgentTask/Event/GateApproval · KnowledgeEntry/ModelConfig/IntegrationConfig（共 13 表）
  - [x] 枚举集中(StrEnum)+ `pg_enum` values_callable 以小写 value 入库；JSON/主键跨方言变体(PG/SQLite)
  - [x] Deliverable JSONB payload + 按 type 的 Pydantic 校验注册表(`schemas/deliverable.py`，已注册 2 示例)
  - [x] 自动生成初始迁移并应用；`alembic check` = 模型==迁移==DB 一致；downgrade 显式 DROP TYPE 修复 PG enum 残留
  - [x] 验证：upgrade→downgrade base(枚举残留 0)→re-upgrade 往返干净；单测 13 passed；ruff 通过；backend 重建后 healthy、alembic 在 head

### ⛳ 检查点 A（T1–T3 后）
- [x] 五服务健康、迁移干净、模型测试通过
- [x] **人工确认数据模型契约** ✅ 已确认（继续 T4）

- [ ] **T4 认证 + RBAC 垂直切片**（M，依赖：T3）
  - [x] 后端 JWT 登录 / `/me` / `require_role` 守卫（401/403）+ bcrypt 密码哈希
  - [x] v1 角色 = `admin`(系统配置/账号CRUD/质量门策略/用户管理) + `user`(日常使用)，枚举可扩展
  - [x] admin-only 用户管理接口(列表/创建)；初始管理员种子 `app/seed.py`
  - [x] 前端登录页 + 应用外壳(antd Layout) + 角色菜单(admin 见用户管理/系统配置) + 受保护/admin 路由守卫 + bootstrap `/me` + 登录态持久化(localStorage)/登出
  - [x] 明暗双主题(antd algorithm 切换+持久化) + AA 基线
  - [x] 验证：后端 23 passed(security/auth/RBAC)+ ruff；前端 eslint+tsc+vite build(vendor 分块)；curl 实测 login/me/401/403/用户创建全绿；前端→nginx→后端 代理登录端到端通；五服务 healthy

- [ ] **T5 模型网关 + DeepSeek + 成本记录**（M，依赖：T3）
  - [x] `LLMGateway` 接口 + `DeepSeekAdapter`（真实 httpx）+ 路由/兜底/重试骨架
  - [x] 每次调用记录模型/Token/成本（`llm_calls` 表 + 迁移）；per-Agent ModelConfig 选模型
  - [x] 受保护 `POST /llm/ping` 联调端点
  - [x] 验证：`test_llm_gateway`(路由/兜底/成本记账/全失败)5 例通过(共 28 passed)；ruff；迁移往返干净
  - [x] /llm/ping 链路实测：认证→网关→适配器→记账→错误处理通；**真实成功调用待填 DEEPSEEK_API_KEY**

- [ ] **T6 事件总线 + arq Worker + 事件溯源**（M，依赖：T3）
  - [x] arq worker + 事件发布/订阅封装 + `Event` 落库 + 失败重试(max_tries=3)
  - [x] WebSocket `/ws/events`(订 Redis pub/sub 转发)；前端 `useEventStream`
  - [x] compose 加 worker 服务(禁继承的 HTTP 健康检查)
  - [x] 验证：单测 3 例(订阅/分发/入队 mock)(共 31 passed)+ ruff；**端到端实测**：POST /events/demo→arq 入队→worker 消费→Event 落库→WebSocket 客户端实时收到(id/type/payload)

- [ ] **T7 编排引擎骨架（状态机 + Dummy Agent + 质量门）**（L，依赖：T6/T3/T5）
  - [x] `BaseAgent` + `DummyAgent`（产出 schema 校验交付物）
  - [x] ContentItem/AgentTask 状态机（PIPELINE：定位→自动门→编导→强制门），事件驱动推进
  - [x] 质量门节点：强制门 blocked + GateApproval 待审 → 审批 → 续跑；自动门放行；幂等防重复
  - [x] 验证：`test_orchestrator` 4 例(流转/版本化/门阻塞/审批续跑/驳回/幂等)(共 35 passed)+ ruff；**端到端实测**：建内容(draft)→启动(2 Agent done+交付物 v1+自动门放行+强制门 pending=blocked)→审批(→published)；6 个编排事件经 arq 总线按序落库

- [ ] **T8 前端流水线看板 + 项目/账号 CRUD + 实时**（L，依赖：T4/T7）
  - [x] 后端补 Project / AccountGroup / Account CRUD（RBAC：list 任意登录用户，增删改限 admin，org 隔离）+ 聚合 `GET /gates` 待审门视图
  - [x] 项目/账号(含 AccountGroup) CRUD，走 TanStack Query；Accounts 页接真实账号 API（分组筛选/就地建组/授权弹窗）
  - [x] Kanban 看板按 ContentItem/current_stage 渲染，新建内容即启动流水线，WebSocket 事件到达即 invalidate 刷新
  - [x] 待审质量门页接真实 `GET /gates` + 一键审批（强制门高亮），审批结果实时刷新看板
  - [x] 验证：后端 41 passed（+6 workspace CRUD/RBAC）+ ruff；前端 tsc + eslint + vite build 全绿
  - [ ] 待补：`pnpm test`/`pnpm e2e` 冒烟（测试框架尚未搭）；双窗口 WebSocket 实时同步人工实测

### ⛳ 检查点 B（M0 完成）
- [ ] `docker compose up` 一键全栈；登录 + 角色菜单
- [ ] 建项目/账号 → Dummy 链路自动推进 → 门阻塞 → 审批 → 继续，看板实时
- [ ] DeepSeek 网关调用成功 + 成本入账
- [ ] 测试覆盖基线达标 + M0 冒烟 e2e
- [ ] **人工评审 M0 → 细化 M1**

---

## Phase M1 — 创作闭环（已细化，2026-06-29 检查点 B 后）

> 目标：定位→编导→美术→视频→剪辑→运营 六阶段端到端自动跑通，默认 DeepSeek，交付物版本化，
> 6 道质量门接入，共享知识库 + 富可视化复盘看板，真实接 Seedance + 抖音。
> 实现顺序：E1 → E2(01→06 顺序切片) → E3 → 其余按依赖。每个子任务 = 垂直切片，含验收+验证。
> ⚠️ 阻塞前提：① 6 个 system prompt 的权威来源是 `配置表.xlsx`（当前不在仓库）→ E1 先写草稿版、
> 标记 `# TODO: 待配置表校准`；② 抖音接口权限范围未确认（阻塞 E8 真实边界，见决策区）。

### E1 — Agent 基座完善 + Prompt 装载（M，依赖：M0；E2 的前置）
- [ ] `BaseAgent` 升级：注入 `LLMGateway` + system prompt + 输出 schema，新增 `LLMAgent` 基类
      （组装 messages→网关 chat→解析 JSON→`validate_payload` 校验→失败重试/兜底）
- [ ] `app/prompts/` 建目录 + 6 个 `*.md`（01-06），先写草稿 prompt（角色/能力/流程/输出 JSON 标准/协作接口），标 TODO 待校准
- [ ] Prompt 装载器：按 agent_code 读取 .md，缓存；`ModelConfig` 缺失时用默认 deepseek-chat
- [ ] 8 个 Agent 的 `ModelConfig` 种子（seed 扩展，幂等）+ 前端 Config 页接真实模型配置 API
- [ ] 验证：单测 LLMAgent（mock 网关返回合规/非法 JSON，校验通过与重试）；ruff

### E2 — 六个创作 Agent（各自垂直切片：prompt + 输出 schema + 编排接入 + 测试）
> 每个 = 注册 payload schema（`schemas/deliverable.py`）+ 草稿 prompt + LLMAgent 子类 + 接入 PIPELINE + 单测（mock 网关）。
- [ ] **01 定位**：positioning_strategy（已有 schema，替换 DummyAgent 为真实 LLMAgent）
- [ ] **02 编导**：topic_plan + video_script（已有 script schema，补 topic_plan schema）
- [ ] **03 美术**：art_prompt（视觉风格书 + 结构化 AI 提示词，输出喂 Seedance）
- [ ] **04 视频**：video_asset（生成参数计划；真实出片在 E7 接 Seedance，此处先产计划）
- [ ] **05 剪辑**：edited_video（剪辑说明 + 成片清单；真实素材处理在 E9）
- [ ] **06 运营**：review_report（复盘 + 优化建议；数据源接 E6 看板/E8 回流）
- [ ] 验证：每个 Agent 单测（输出 schema 校验 + golden 关键字段断言）；6 个合计纳入 pytest

### E3 — 主链路六阶段自动流转 + 6 道质量门接入（L，依赖：E2）
- [ ] PIPELINE 从 2 步扩到 6 阶段 + 6 道门（Gate1 定位/Gate2 选题=自动；Gate3 脚本/Gate5 发布前=强制；Gate4 成片=自动；Gate6 投流属 M3）
- [ ] 上游交付物注入下游 `AgentContext.upstream`（按 type→payload 解析最新版本）
- [ ] 事件驱动：每阶段 done 自动触发下一阶段；强制门 blocked→审批→续跑（复用 T7 引擎）
- [ ] 验证：`test_orchestrator` 扩展——六阶段全流转、Gate3/Gate5 阻塞与审批、上游引用解析；端到端实测

### E4 — 交付物版本化 / 回滚 / 上游引用解析（M，依赖：E3）
- [ ] 同 type 重跑产新 version（已有唯一约束），旧版置 superseded；回滚接口（指定 version 设回 approved）
- [ ] 交付物历史 + 版本对比 API；前端交付物抽屉（看历史/对比/回滚）
- [ ] 验证：版本递增、回滚、superseded 流转单测；前端 tsc+build

### E5 — 共享知识库读写 + 前端页（M，依赖：M0）
- [ ] `KnowledgeEntry` CRUD API（4 类：爆款/画像/提示词/话术），按 category 过滤，RBAC
- [ ] Agent 可读知识库切片注入 `AgentContext.knowledge`（运营写爆款、客服写话术——M2）
- [ ] Knowledge.tsx 接真实 API（替换 mock，分类标签 + 增删改）
- [ ] 验证：CRUD + 过滤单测；前端 tsc+eslint+build

### E6 — 富可视化复盘看板（L，依赖：E8 数据回流；可先 mock 数据撑结构）
- [ ] `MetricSnapshot` 模型 + 迁移（播放/完播/互动/粉丝/时段，按内容/账号/日期）
- [ ] 复盘聚合 API（日/周/月维度、多平台对比、按项目/账号/内容筛选）
- [ ] ReviewDashboard.tsx 接真实聚合 API（ECharts 多图已搭，替换 mock 数据源）
- [ ] 报告导出（PNG/PDF）；运营复盘交付物可引用看板图表
- [ ] 验证：聚合查询单测；前端图表渲染 + 导出冒烟

### E7 — 真实集成 Seedance（M，依赖：E2-04；已有 Key）
- [ ] `integrations/video_gen/seedance.py` 适配器（真实 API：提交生成→轮询→取素材 URL）
- [ ] 素材落本地卷（MinIO 接口预留）；`MaterialAsset` 模型 + 迁移
- [ ] 04 视频 Agent 接入：美术提示词→Seedance 生成→素材入库→交付物引用
- [ ] 验证：适配器 mock 单测；真实 Key 端到端生成一条素材（手动）

### E8 — 真实集成 抖音（L，依赖：E3；⚠️ 接口权限待确认）
- [ ] `integrations/publish/douyin.py` 适配器（OAuth 授权 + 视频上传/发布）
- [ ] 数据回流：拉取视频/粉丝数据写 `MetricSnapshot`（喂 E6 看板）
- [ ] 06 运营 Agent 接入发布；发布前过 Gate5 强制门
- [ ] 小红书/视频号先 stub（接口同构，预留）
- [ ] 验证：适配器 mock 单测；真实 Key 发布 + 回流（手动，依权限范围裁剪）

### E9 — 执行层：矩阵 + 合规检测 + 素材工具（M，依赖：E3）
- [ ] 合规检测服务（敏感词库 + 原创度检查）→ 接 Gate3 脚本合规（自动预检 + 人工复核）
- [ ] `ComplianceCheck` 模型 + 迁移；脚本完成自动触发预检，结果呈现在审批页
- [ ] 素材工具集骨架（去/加水印、裁剪、转码、GIF——接口先行，实现按需）
- [ ] 矩阵批量下发：账号页"批量下发任务/排期"接真实编排（M0 已留 UI）
- [ ] 验证：合规检测单测（命中敏感词/原创度阈值）；批量下发单测

### E10 — 闭环反馈（M，依赖：E2-06、E3）
- [ ] 运营复盘完成→广播 `optimization.suggestion` 事件；上游 Agent 订阅并在下周期响应
- [ ] 建议→执行→验证追踪：`OptimizationSuggestion` 模型（状态机：建议/已采纳/已验证）
- [ ] 客服需求报告→编导选题输入（需求反哺，客服 Agent 属 M2，此处先留接口）
- [ ] 前端"建议→执行→验证"追踪视图
- [ ] 验证：广播→订阅→响应链路单测；端到端实测

### ⛳ 检查点 C（M1 完成）
- [ ] 六阶段端到端自动流转 + 6 道门（Gate3/Gate5 强制人工）跑通
- [ ] 交付物版本化/回滚/上游引用正确
- [ ] 知识库读写 + 复盘看板真实数据
- [ ] Seedance 真实生成 + 抖音真实发布/回流（依权限范围）
- [ ] 闭环反馈一个完整周期可追踪
- [ ] **人工评审 M1 → 细化 M2**

---

## Phase M2 — 分发与量产（Epic）
- [ ] 分发中心（批量发布 + 定时排期，多平台/多账号）
- [ ] 批量混剪二创去重量产模式
- [ ] 补齐小红书/视频号发布
- [ ] 抖音评论抓取 + 客服 Agent（回复/负面/私域/需求反哺）
- [ ] 接入蝉妈妈/飞瓜补充数据

## Phase M3 — 投流自动化（Epic）
- [ ] 千川 API + 投流 Agent 全自动
- [ ] 自动投放规则引擎（关停/追爆/扩量）
- [ ] ROI 接入复盘看板 + Gate6 大额投放强制人工

## Phase M4 — 全自动无人值守 + 优化（Epic）
- [ ] 全链路事件/定时触发
- [ ] 告警 + 成本看板
- [ ] A/B + 内容效果预测模型

---

## 待人工决策（阻塞项见 plan.md 第十一节）
- [x] 团队角色 → **已确认：`admin` + `user` 两级，可扩展**
- [x] 对象存储起步 → **已确认：本地磁盘卷 + MinIO 接口预留**
- [ ] **配置表 xlsx 缺失**（阻塞 E1 prompt 权威来源）→ 现策略：先写草稿 prompt 标 TODO，拿到表后校准
- [ ] **抖音 API 能力范围**（阻塞 E8 真实边界）→ 需确认应用已申请通过哪些接口权限（至少"视频发布+数据分析"才能跑 E8 真实，否则先 stub）
- [ ] 检查点 A：数据模型契约确认
