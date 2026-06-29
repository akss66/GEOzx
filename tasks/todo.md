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
- [x] `BaseAgent` 升级：`run(session, org_id, ctx)` 签名；新增 `LLMAgent` 基类
      （组装 messages→网关 chat→`extract_json` 抽取→`validate_payload` 校验→失败带错误重试 1 次）
- [x] `app/prompts/` 建目录 + 6 个草稿 prompt（01-06，含输出 JSON 标准），均标 TODO 待配置表校准
- [x] Prompt 装载器 `agents/prompts.py`（按名读 .md + 缓存）；engine 调用点改传 session/org_id（经 project 解析 org）
- [x] 8 个 Agent 的 `ModelConfig` 种子（seed 扩展，幂等，已入库验证）+ 后端 `/model-configs` API + 前端 Config 页接真实模型配置（首选/兜底可改并持久化）
- [x] 验证：后端 51 passed（+10：LLMAgent JSON 抽取/校验/重试 + model-config CRUD/RBAC）+ ruff；前端 tsc+eslint+build 全绿；`/model-configs` 实测返回 8 条
- [ ] 待补：DeepSeek 真实驱动单个 Agent 跑通（属 E2-01 切片）

### E2 — 六个创作 Agent（各自垂直切片：prompt + 输出 schema + 编排接入 + 测试）
> 每个 = 注册 payload schema（`schemas/deliverable.py`）+ 草稿 prompt + LLMAgent 子类 + 接入 PIPELINE + 单测（mock 网关）。
- [x] **01 定位**：positioning_strategy（PositioningAgent，真实跑通）
- [x] **02 编导**：video_script（ContentAgent，读上游定位，真实跑通）
- [x] **03 美术**：art_prompt（ArtAgent，schema 注册 + 接入，随 E3 真实跑通）
- [x] **04 视频**：video_asset（VideoAgent，生成参数计划，随 E3 真实跑通）
- [x] **05 剪辑**：edited_video（EditingAgent，随 E3 真实跑通）
- [x] **06 运营**：review_report（OperationAgent，随 E3 真实跑通）
- [ ] 待补：02 topic_plan（选题方案）schema；各 Agent 的 golden 关键字段单测（当前靠 E3 整链测试覆盖）

### E3 — 主链路六阶段自动流转 + 6 道质量门接入（L，依赖：E2）
- [x] PIPELINE 扩到六阶段 + 5 道门（Gate1 定位/Gate2 选题/Gate4 成片=自动；Gate3 脚本/Gate5 发布前=强制；Gate6 大额投放属 M3 并行投流链路）
- [x] 上游交付物经 `AgentContext.upstream` 注入下游（LLMAgent.build_user_message 组装）
- [x] 事件驱动：每阶段 done 自动触发下一阶段；强制门 blocked→审批→续跑（复用 T7 引擎）
- [x] 验证：`test_orchestrator` 扩展（六阶段全流转、Gate3/Gate5 阻塞与续跑、上游引用断言）52 passed+ruff；**真实端到端**：六阶段 DeepSeek 全跑通，2 道强制门审批续跑，6 份交付物各过 schema 校验，published；12 次调用累计 $0.0078 入账

### E4 — 交付物版本化 / 回滚 / 上游引用解析（M，依赖：E3）
- [x] 同 type 重跑产新 version（`rerun_stage`），旧版自动 superseded；`_upstream` 改为只取最新生效版（修多版本覆盖隐患）
- [x] 回滚接口（`rollback_deliverable`：指定 version 设回 approved，其余 superseded）+ 历史 API（含 superseded）
- [x] 前端交付物抽屉（DeliverableDrawer：按 type 分组看历史/payload/状态 + 重跑 + 回滚），看板卡片点击打开
- [x] 验证：58 passed（+3：重跑 supersede/回滚/上游取最新版）+ruff；前端 tsc+eslint+build；**真实端到端**：启动 v1→重跑产 v2(v1 superseded)→回滚 v1(v1 approved/v2 superseded)，状态机正确

### E5 — 共享知识库读写 + 前端页（M，依赖：M0）
- [x] `KnowledgeEntry` CRUD API（4 类：爆款/画像/提示词/话术），按 category 过滤，org 隔离（全体可读可写）
- [x] Agent 读知识库切片注入 `AgentContext.knowledge`（engine._knowledge 按 category 分组，上限 20 条）
- [x] Knowledge.tsx 接真实 API（Tabs 分类 + 卡片网格 + 新增/编辑/删除 + 标签）
- [x] 验证：55 passed（+3 CRUD/过滤）+ruff；前端 tsc+eslint+build；**真实端到端**：建 3 类条目→list/过滤正确→注入定位 Agent 输入（含"知识库参考"+爆款条目标题）

### E6 — 富可视化复盘看板（L，依赖：E8 数据回流；可先 mock 数据撑结构）
- [x] `MetricSnapshot` 模型 + 迁移（播放/曝光/完播/点赞/评论/转发/涨粉，按内容/账号/日期；含 PG enum DROP TYPE）
- [x] 复盘聚合 API（`/metrics/overview` 趋势/完播互动/排名/汇总，days 窗口）+ 录入 API（`/metrics/ingest`，E8 回流写入口，幂等）
- [x] ReviewDashboard 接真实：流量趋势/完播互动/内容排名 3 图 + 汇总卡 + 空态；其余（热力/雷达/ROI/情感/漏斗）标"示例数据"待 M2/M3/多平台回流
- [x] 报告导出：流量趋势图 PNG 导出（ECharts getDataURL）
- [x] 验证：65 passed（+3：空态/聚合/幂等回流）+ruff；迁移 alembic check 一致；前端 tsc+eslint+build；真实环境 ingest→overview 聚合（趋势/总播放/平均完播/净增粉/排名）正确
- [ ] 待补：完播互动/排名图的 PNG 导出；周/月维度精确分桶（当前按 days 窗口）；运营复盘交付物引用看板

### E7 — 真实集成 Seedance（M，依赖：E2-04；已有 Key）
- [x] `integrations/video_gen/` 接口（VideoGenAdapter 可切换后端）+ `ArkVideoAdapter`（火山方舟，提交→轮询→取 URL）
- [x] 04 视频 Agent 接入：LLM 产计划 → 用上游美术提示词调 Ark 真实出片 → video_url 写入交付物；无 key/失败降级不阻断
- [x] 验证：68 passed（+3：适配器 mock + 降级 + 出错）+ruff；**真实出片跑通**：豆包 Seedance 1.0-pro 约 50s 出片，返回火山 TOS 视频 URL；完整六阶段流水线含真实出片端到端跑通
- [x] ✅ 已改异步：出片改 arq 后台任务（agent.done→入队 generate_video），编排不再阻塞、HTTP 不再超时（审批续跑秒级返回）
- [x] ✅ 已落卷：worker 下载视频落本地卷 objects:/data/objects + MaterialAsset 记录 + `/materials/{id}/file` 播放接口（不再 24h 过期）；前端 DeliverableDrawer 出片状态 + 视频播放
- [x] 验证（异步）：68 passed（VideoAgent 只产计划 + 出片任务下载/落卷/失败降级）+ruff；迁移 alembic check 一致；**真实异步出片**：审批续跑 34s 返回(视频阶段秒过 gen_status=queued)→后台 worker 出片 ready→videos/14/1.mp4(5.3MB)→播放接口 200 video/mp4
- [ ] 待补：图生视频（首帧）；素材列表页；MinIO 替换本地卷

### E8 — 真实集成 抖音（L，依赖：E3；⚠️ 接口权限待确认）
- [ ] `integrations/publish/douyin.py` 适配器（OAuth 授权 + 视频上传/发布）
- [ ] 数据回流：拉取视频/粉丝数据写 `MetricSnapshot`（喂 E6 看板）
- [ ] 06 运营 Agent 接入发布；发布前过 Gate5 强制门
- [ ] 小红书/视频号先 stub（接口同构，预留）
- [ ] 验证：适配器 mock 单测；真实 Key 发布 + 回流（手动，依权限范围裁剪）

### E9 — 执行层：矩阵 + 合规检测 + 素材工具（M，依赖：E3）
- [x] 合规检测服务（内置词库：绝对化用语/违禁词=block，导流/夸大=warn）→ 接 Gate3 脚本合规
- [x] `ComplianceCheck` 模型 + 迁移（含 PG enum 显式 DROP TYPE）；脚本门阻塞自动触发预检，落库
- [x] 审批页呈现合规预检结果（ComplianceBanner：风险等级 + 命中词标签）；board/`/gates` API 带出 compliance
- [x] 验证：62 passed（+4：词库 pass/warn/block + 引擎集成）+ruff；迁移 alembic check 一致；前端 tsc+eslint+build；真实环境 Gate3 预检落库 + block 分支命中绝对化用语
- [ ] 待补（延后）：矩阵批量下发任务/排期（依赖 M2 分发）；素材工具集（去/加水印/裁剪/转码/GIF，按需）；可配置词库；原创度检测

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
