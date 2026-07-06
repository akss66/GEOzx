# 同舟行 TODO：系统重整与产品收敛清单

> 配套计划：`tasks/plan.md`
> 来源规格：`SPEC.md`
> 当前阶段：**前端产品界面体系重建 P1**
> 状态图例：`[ ]` 未完成，`[x]` 已完成

---

## 2026-07-03 前端产品界面体系重建 P1

- [x] 用户确认产品名：同舟行 AI 新媒体运营平台；DyFlow 弱化为内部代号。
- [x] 用户确认默认首页：运营大脑。
- [x] 用户确认视觉方向：冷白 / 雾白磨砂纸质感 + 黑白灰极简。
- [x] 用户确认布局方向：升级版左侧固定导航 + 顶部轻工具栏 + 大工作区。
- [x] 用户确认登录页方向：官网首屏型，但克制、不做夸张营销。
- [x] 用户确认核心交互：主 Agent 对话输入后，逐步调度专家 Agent。
- [x] 用户确认专家呈现：专业身份卡片，不使用真人头像。
- [x] 用户确认专家结果：一句核心结论 + 可展开详情。
- [x] 用户确认第一阶段范围：全局统一设计风格，重点深做登录页、AppShell、运营大脑；其他页面先纳入统一风格。
- [x] 用户确认 Agent 编排第一阶段：先做高保真前端演示流程，但保留真实接口。
- [x] 更新 `PRODUCT.md`。
- [x] 更新 `DESIGN.md`。
- [x] 更新 `SPEC.md` 中 UI 与路线图相关条目。
- [x] 重写 `FRONTEND_REDESIGN_PLAN.md`。
- [x] 同步 `tasks/plan.md` 与 `tasks/todo.md`。
- [ ] 重建前端设计令牌与全局 CSS。
- [ ] 重构 AppShell、左侧导航、顶部轻工具栏。
- [ ] 重构登录页。
- [ ] 重构运营大脑首页。
- [ ] 新增专家 Agent 编排演示组件与 adapter。
- [ ] 统一其他页面基础视觉风格。
- [ ] 运行前端构建与浏览器 QA。
- [ ] 用户确认后部署到 `https://dyflow.tzxai.top`。

## 2026-07-01 执行顺序调整

- [x] 用户确认：从现在开始改为后端先行。
- [x] 已完成前端 F1-F4/F6 保留为后端契约参照。
- [x] 当前优先级：B1 数据模型与迁移第一版。
- [x] 下一优先级：B2 运营大脑 API 第一版。
- [x] 再下一优先级：B3 专家团 API 第一版。
- [ ] 前端 F5/F7-F9 后置到后端契约稳定后继续。

---

## 已完成基线摘要

- [x] 后端：认证、RBAC、项目/账号 CRUD、事件总线、arq worker、WebSocket、旧六阶段编排、质量门、交付物版本、知识库、复盘指标、闭环反馈基础。
- [x] 后端：六阶段 Agent 主链路可跑通，Seedance 异步出片已接入并可落本地素材卷。
- [x] 前端：登录、应用外壳、运营大脑、专家团、任务/验收、账号矩阵、知识库、复盘、成本、配置页已有可用基础。
- [x] 规格：`SPEC.md` 已整改为“运营大脑 / 决策 Agent + 8 个专业子 Agent”的专家团系统。
- [x] 计划：`tasks/plan.md` 已改为“后端先行”。

---

## Phase B：后端先行

### B1 数据模型与迁移

- [x] 新增 `BrainTask`。
- [x] 新增 `TaskBrief`。
- [x] 新增 `OrchestrationPlan`。
- [x] 新增 `AgentInvocation`。
- [x] 新增 `DeliverableAcceptance`。
- [x] 新增 `AutomationPolicy`。
- [x] 新增后端枚举：`AgentCode`、`AgentGroup`、`BrainTaskStatus`、`BrainTaskType`、`AgentInvocationStatus`、`DeliverableAcceptanceStatus`、`RerunScope`、`AutomationLevel`。
- [x] 新增 Alembic 迁移文件。
- [x] 模型导入 `app.models`，测试库可通过 `Base.metadata.create_all` 创建。
- [x] 任务最终验收后可关闭本次任务上下文，不做长期记忆。
- [x] 验证：`python -m pytest` 通过。
- [x] 验证：`python -m ruff check app tests` 通过。
- [x] 验证：真实 Postgres 上 `alembic upgrade head` 通过。
- [ ] 验证：真实 Postgres 上 downgrade/upgrade 往返干净。

### B2 运营大脑 API

- [x] `POST /brain/tasks/draft`
- [x] `POST /brain/tasks/{id}/confirm`
- [x] `GET /brain/tasks`
- [x] `GET /brain/tasks/{id}`
- [x] `GET /brain/tasks/{id}/invocations`
- [x] `GET /brain/tasks/{id}/acceptances`
- [x] `POST /brain/tasks/{id}/accept`
- [x] `POST /brain/tasks/{id}/rerun`
- [x] `POST /brain/tasks/{id}/rejudge`
- [x] `POST /brain/tasks/{id}/close-memory`
- [x] API 输出对齐前端 `BrainTask`、`TaskBrief`、`OrchestrationPlan`、`AgentInvocation`、`DeliverableAcceptance` 契约。
- [x] 验证：`backend/tests/test_brain_api.py` 通过。
- [x] 验证：前端 `frontend/src/api/brain.ts` 可切换真实 API。

### B3 专家团 API

- [x] `GET /agents`
- [x] `GET /agents/{code}`
- [x] `PATCH /agents/{code}/config`
- [x] `POST /agents/{code}/invoke`
- [x] 返回 1 个运营大脑 + 8 个子 Agent。
- [x] 管理员可配置 Agent 模型、兜底模型、自动化级别。
- [x] 普通成员不能修改 Agent 配置。
- [x] 直接调用子 Agent 的结果写入 `AgentInvocation` 并回流运营大脑。
- [x] 验证：`backend/tests/test_agents_api.py` 通过。
- [x] 验证：前端专家团页可从真实 API 获取状态、配置、当前任务。

### B4 编排引擎适配新任务模型

- [x] 保留当前六阶段 pipeline 为默认参考链路。
- [x] `BrainTask.confirm` 后可触发现有编排引擎。
- [x] 每次子 Agent 调用写入 `AgentInvocation`。
- [x] `AgentInvocation` 记录输入摘要、输出摘要、模型、token、成本、失败原因、上下游依赖。
- [x] 动态计划草稿支持并行依赖与跳过节点（内容生产六节点；诊断/复盘跳过视频相关节点）。
- [ ] 支持动态计划执行：返工、部分重跑、按 `rerun_scope` 影响上游/下游。
- [x] 分项验收结果影响运营大脑下一步调度。
- [x] 质量门阻塞时更新 `BrainTask.status/current_focus`。
- [x] 验证：新增编排适配测试。
- [x] 验证：`backend/tests/test_brain_api.py` 覆盖动态计划并行/跳过节点并通过。
- [x] 验证：旧 `tests/test_orchestrator.py` 继续通过。
- [ ] 验证：全量 `python -m pytest` 需在审批链路恢复后重跑确认。

### B5 LiteLLM / LangGraph / MCP 阶段接入

- [x] LiteLLM 替换或包装 `LLMGateway` 底层实现，业务接口保持稳定。
- [x] LangGraph 作为复杂任务、人机确认、可恢复执行场景的 adapter。
- [x] MCP 先做内部 `ToolAdapter` 白名单。
- [x] MCP 工具具备 RBAC。
- [x] MCP 工具具备参数校验。
- [x] MCP 工具具备审计记录。
- [x] MCP 工具具备超时控制。
- [x] 默认禁止任意命令/文件访问工具。
- [x] 验证：LLM gateway 测试通过。
- [x] 验证：工具白名单与权限测试通过。

### B6 多平台账号矩阵与分发中心

- [x] 任务 Brief 支持绑定项目。
- [x] 任务 Brief 支持绑定账号组。
- [x] 任务 Brief 支持绑定平台范围。
- [x] 任务 Brief 支持绑定账号范围。
- [x] 抖音标记真实接入状态、授权状态、数据回流状态。
- [x] 小红书标记半自动/手动回填状态。
- [x] 视频号标记半自动/手动回填状态。
- [x] 分发动作写入事件与审计记录。
- [x] 验证：账号矩阵 API 测试通过。
- [x] 验证：任务 Brief 绑定账号范围测试通过。

---

## Phase F：前端后置收口

### F-API 前端切真实 API

- [x] `frontend/src/api/brain.ts` 支持调用真实 `/brain/*`。
- [x] `frontend/src/api/agents.ts` 支持调用真实 `/agents/*`。
- [x] 运营大脑首页真实调用 draft / confirm / list。
- [x] 专家团页真实调用 list / detail / config / invoke。
- [x] 分项验收真实调用 acceptances / accept / rerun / rejudge / close-memory。
- [ ] 保留开发 mock fallback 或开关。
- [x] 验证：`npm.cmd run lint` 通过。
- [x] 验证：`npm.cmd run build` 通过。
- [x] 验证：Docker 前端真实登录 -> 生成 Brief -> 确认执行 -> 专家团页面冒烟通过。

### F5 Trace / React Flow

- [x] 确认并安装 `@xyflow/react`。
- [x] 新建 `frontend/src/components/flow/`。
- [x] 用真实 `AgentInvocation` 渲染基础 Agent 调用链。
- [x] 用真实数据补齐质量门、人工确认、交付物、返工边。
- [x] 支持“简洁过程 / 完整 Trace”切换。
- [x] 点击节点展示输入摘要、输出摘要、模型、token、成本、失败原因、上下游依赖。
- [x] 已补 Playwright 专项用例覆盖缩放、节点拖拽、节点非重叠断言。
- [x] 已将拖拽断言改为校验 React Flow 节点自身坐标，避免 768 宽度下页面横向位移误报。
- [ ] 验证：768/1440 宽度可缩放、拖拽、节点不重叠。

### F7 账号矩阵任务绑定

- [x] 账号矩阵支持项目/品牌到人设/赛道组再到平台账号层级展示。
- [x] 支持按项目、赛道、人设、平台、账号组筛选。
- [x] 运营大脑创建任务时可绑定账号组、平台、账号范围。
- [x] 筛选、空态、授权异常提示可用。

### F8 复盘闭环、成本、风险整合

- [x] 复盘建议可一键送入运营大脑。
- [x] 被采纳建议生成下一轮任务 Brief 或重跑建议。
- [x] 成本页增加按运营大脑、子 Agent、任务维度查看成本。
- [x] 风险队列整合质量门、授权过期、模型失败、平台回流失败。

### F9 前端测试与浏览器 QA

- [x] 补核心 Hook/Store 的 Vitest 测试。
- [x] 补核心应用路由的 Vitest 冒烟测试。
- [x] 补核心组件的 Testing Library 测试。
- [x] 补 Playwright 冒烟：登录 -> 运营大脑创建 Brief -> 确认计划 -> 查看专家团 -> 分项验收/打回。
- [x] 浏览器检查 320、768、1024、1440 宽度。
- [x] Console 无运行时错误。
- [x] 验证：`npm.cmd run test` 通过。
- [x] 验证：`npm.cmd run lint`、`npm.cmd run build` 通过。

---

## 检查点

### P1 后端契约成型

- [x] B1-B3 第一版完成。
- [x] 后端全量测试通过：`python -m pytest`。
- [x] 后端全量 lint 通过：`python -m ruff check app tests`。
- [x] 真实 Postgres migration upgrade 验证通过。
- [ ] 真实 Postgres migration downgrade/upgrade 往返验证通过。
- [x] 前端 API adapter 切到真实 API。

### P2 后端编排闭环

- [ ] B4 完成。
- [x] 运营大脑确认任务后可触发真实编排。
- [x] 分项验收、重跑、重判会影响下一步调度。
- [x] 旧六阶段 pipeline 测试继续通过。

### P3 开源组件接入边界

- [x] B5 完成。
- [x] LiteLLM adapter 可用。
- [x] LangGraph adapter 有最小可恢复执行示例。
- [x] MCP 工具白名单、RBAC、审计和超时测试通过。

### P4 多平台与前端收口

- [x] B6 完成。
- [ ] F-API/F5/F7/F8/F9 完成。
- [x] `docker compose up` 后可演示完整端到端流程。

---

## 当前阻塞 / 待确认

- [ ] 是否在隔离库补跑真实 Postgres 的 Alembic downgrade/upgrade 往返验证。
- [ ] 前端真实 API 切换采用环境变量开关，还是直接替换 mock adapter。
- [ ] B4 `rerun_scope` 上游/下游/全链路执行语义需继续补；上次修改 `brain_adapter.py` 被审批链路中断。
- [x] React Flow 依赖已安装并接入；F5 仍需拖拽/节点重叠专项验证。
- [ ] F5 Playwright 专项用例已补并修正断言，需重跑通过后再关闭验证项。
- [ ] 小红书、视频号 OAuth 真实接入排期。

## 2026-07-02 计划调整

- [x] 用户确认：投流 / 千川 / 大额投放自动化先不做。
- [x] 本轮只保留投流专家、模型字段、菜单与适配器预留，不进入近期实现顺序。
- [x] 已完成系统重整审查：见 `SYSTEM_RESET_AUDIT.md`。
- [x] S1 导航与范围收敛：隐藏投流、客服等暂缓模块，主导航只保留当前 MVP 可完成能力。
- [x] 用户确认：抖音 / 小红书 / 视频号接入优先级高于继续打磨纯内部编排。
- [ ] P0 平台接入底座：统一三平台账号授权状态、外部账号 ID、接入状态、数据同步状态和数据回流入口。
  - [x] 账号矩阵前端重构为三平台接入控制台：平台接入状态总览、矩阵结构、账号明细三层。
  - [x] 管理员可在账号明细中手动标记授权完成 / 数据回流正常，先支撑半自动接入。
  - [x] 后端补正式 `platform_integrations` / `platform_account_auths` 接入配置与账号授权模型，避免长期把接入状态藏在 `Account.auth` JSON。
  - [x] 后端新增 `/platform-integrations` 查询与配置 API，抖音默认能力按官方文档拆分为 OAuth、JS SDK 签名、JSBridge、H5 分享、OpenAPI。
  - [x] 旧账号“标记授权/回流正常”动作同步写入正式 `platform_account_auths`，前端半自动流程暂不破坏。
  - [x] 前端账号矩阵改接 `/platform-integrations`，平台层可配置 ClientKey、ClientSecret 存储引用、回调地址、安全域名与权限范围，不再只从账号汇总推导。
- [ ] P1 抖音优先接入：账号授权/识别、基础指标回流；发布先做可审计半自动记录。
  - [x] 抖音平台配置默认使用 `vault://dyflow/douyin/client-secret` 密钥引用，前端不写死真实 Secret。
  - [x] 后端新增抖音 OAuth 授权链接生成接口，使用服务端签名 `state` 绑定 org/account。
  - [x] 后端新增抖音 OAuth callback，回调后写入 `platform_account_auths`，token 仅保存 vault 引用，不回显。
  - [x] 后端新增抖音 JS SDK 签名接口，服务端获取/缓存 ticket 并返回签名材料。
  - [x] 前端账号矩阵抖音账号支持“官方授权”动作，打开后端生成的官方授权 URL。
  - [ ] 接真实 Vault 客户端或生产部署 secret resolver。
  - [ ] 接抖音用户信息/粉丝/内容基础指标回流。
  - [ ] 发布能力继续保持可审计半自动记录，真实发布等开放平台权限确认后推进。
- [ ] P2 小红书接入：先做半自动/手动回填与授权状态管理，再按开放平台权限接官方 API。
- [ ] P3 视频号接入：先做半自动/手动回填与授权状态管理，再按开放平台权限接官方 API。
- [ ] S2 运营大脑主流程重排：突出“目标 -> Brief -> 调度 -> 交付物 -> 验收 -> 复盘建议 -> 下一轮任务”。
- [ ] S3 清理示例数据露出：复盘只保留真实指标与优化建议闭环，示例图隐藏或降级。
- [ ] S4 专家团真实感修正：当前任务从真实 `BrainTask` / `AgentInvocation` 聚合，直接调用降级为调试入口。
- [ ] S5 工程验证回补：产品收敛后再处理 B4 `rerun_scope`、F5 Playwright、Postgres downgrade/upgrade。
