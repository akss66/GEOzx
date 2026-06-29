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
  - [ ] 项目/账号(含 AccountGroup) CRUD，走 TanStack Query
  - [ ] Kanban 看板按 ContentItem/AgentTask 渲染，WebSocket 实时刷新
  - [ ] 待审质量门醒目高亮 + 一键审批
  - [ ] 验证：`pnpm test` + `pnpm e2e` M0 冒烟；双窗口实时同步

### ⛳ 检查点 B（M0 完成）
- [ ] `docker compose up` 一键全栈；登录 + 角色菜单
- [ ] 建项目/账号 → Dummy 链路自动推进 → 门阻塞 → 审批 → 继续，看板实时
- [ ] DeepSeek 网关调用成功 + 成本入账
- [ ] 测试覆盖基线达标 + M0 冒烟 e2e
- [ ] **人工评审 M0 → 细化 M1**

---

## Phase M1 — 创作闭环（中粒度，检查点 B 后细化为可实现任务）

- [ ] E1 Agent 基座完善 + 6 个 system prompt 装载（源自配置表）
- [ ] E2 六个创作 Agent（各自垂直切片：prompt + 输出 schema + 编排接入 + 测试）
  - [ ] 01 定位　[ ] 02 编导　[ ] 03 美术提示词　[ ] 04 视频创作　[ ] 05 剪辑　[ ] 06 运营
- [ ] E3 主链路六阶段自动流转 + 6 道质量门接入（Gate3/Gate5 强制人工）
- [ ] E4 交付物版本化 / 回滚 / 上游引用解析
- [ ] E5 共享知识库（爆款/画像/提示词/话术）读写 + 前端页
- [ ] E6 富可视化复盘看板（ECharts 多图 + 日/周/月 + 报告导出）
- [ ] E7 真实集成 Seedance（视频生成，素材落存储）
- [ ] E8 真实集成 抖音（发布 + 数据回流；其余平台 stub）
- [ ] E9 执行层：矩阵管理 + 合规检测服务(接 Gate3) + 素材工具集
- [ ] E10 闭环反馈（optimization.suggestion 广播 + 建议→执行→验证 追踪）

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
- [ ] 抖音 API 能力范围（阻塞 M1 E8 真实边界）
- [ ] 检查点 A：数据模型契约确认
