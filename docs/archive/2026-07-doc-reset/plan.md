# 同舟行系统重整与产品收敛计划

> 来源：`SPEC.md`、当前代码、已完成前端 F1-F4/F6、用户 2026-07-01 明确要求“后端先行”。
> 当前方向：2026-07-02 起先做系统重整与产品收敛，把运营大脑主闭环做成可信 MVP，再回补后端编排细节与验证。
> 2026-07-02 计划调整：投流 / 千川 / 大额投放自动化先不做，只保留模型、菜单和适配器预留；近期不把投流作为交付目标。
> 2026-07-03 计划调整：抖音授权链路已跑通后，当前优先级切换为前端产品界面体系重建。默认首页为运营大脑，视觉方向改为冷白磨砂纸质感 + 黑白灰极简；第一阶段先统一全局风格、登录页、AppShell、运营大脑和专家 Agent 编排演示流程。

---

## 当前优先级：前端产品界面体系重建 P1

目标：

- 把产品名统一为“同舟行 AI 新媒体运营平台”，DyFlow 仅作为内部代号弱化保留。
- 将旧的深色金属银默认方向替换为冷白 / 雾白 / 石墨黑 / 细灰线的黑白灰极简方向。
- 登录后默认进入运营大脑，而不是传统 dashboard。
- 运营大脑首屏采用主 Agent 对话输入 + 专家 Agent 编排任务流。
- 专家 Agent 使用专业身份卡片，不使用真人头像；完成后默认一句核心结论，详情可展开。
- 第一阶段可先做高保真前端演示流程，但必须预留真实后端 Agent 接口。

执行顺序：

1. 文档与设计源头对齐：`PRODUCT.md`、`DESIGN.md`、`SPEC.md`、`FRONTEND_REDESIGN_PLAN.md`、`tasks/plan.md`、`tasks/todo.md`。
2. 重建设计令牌与全局 CSS。
3. 重构 AppShell、左侧导航、顶部轻工具栏。
4. 重构登录页。
5. 重构运营大脑首页与专家编排组件。
6. 将其他页面纳入统一视觉体系。
7. 浏览器 QA、构建验证、按需部署到公网。

## 一、目标

把系统从“固定六阶段内容流程”升级为：

> **运营大脑 / 决策 Agent + 8 个专业子 Agent 的专家团系统**

本轮执行顺序改为 **后端先行**：

1. 先补真实数据模型、迁移、API、测试。
2. 再适配现有编排引擎，把六阶段 pipeline 降级为默认参考链路。
3. 再接 LiteLLM、LangGraph、MCP 等基础设施。
4. 最后让前端从 mock adapter 切到真实 API，继续完成 Trace、账号矩阵、复盘闭环和浏览器 QA。

已完成的前端 F1-F4/F6 不丢弃，作为后端 API 契约和体验验收参照。

---

## 二、当前基线

### 已有能力

- 后端已有认证、RBAC、项目/账号 CRUD、事件总线、WebSocket、六阶段编排引擎、质量门、交付物版本、知识库、复盘指标、Seedance 异步出片、闭环反馈基础。
- 前端已有登录、应用外壳、运营大脑首页、专家团页、任务/验收、账号矩阵、知识库、复盘、成本、配置页基础。
- SPEC 已确认产品形态：运营大脑 + 8 个子 Agent，支持动态调度、分项验收、重跑、任务记忆关闭。

### 当前问题

- 旧编排仍以固定 pipeline 为中心，还不能真实表达运营大脑的动态调度。
- 专家团目前前端体验已成型，但后端 API 与真实状态仍需补齐。
- 分项验收、重跑范围、运营大脑重判需要落库并影响下一步调度。
- 多账号、多平台矩阵还没有成为任务 Brief 的真实后端约束。
- LiteLLM、LangGraph、MCP 已进入 SPEC，但需要以 adapter 方式逐步接入，避免破坏现有稳定接口。

---

## 三、执行原则

1. **后端先行**：数据库模型、API、测试先落地，前端随后切真实接口。
2. **契约兼容**：后端字段优先对齐前端已定义的 `BrainTask`、`TaskBrief`、`OrchestrationPlan`、`AgentProfile`、`AgentInvocation`、`DeliverableAcceptance`。
3. **保留旧链路**：现有六阶段 pipeline 不删除，作为运营大脑可调用的默认参考链路。
4. **垂直切片**：每一阶段都要有模型、API、测试或可验证命令，不只写空壳。
5. **渐进接入开源组件**：LiteLLM、LangGraph、MCP 先通过 adapter/白名单/边界校验接入。
6. **任务记忆边界明确**：用户最终验收后关闭本次任务上下文；历史记录以后做，现在不做长期记忆。
7. **投流后置**：投流专家、千川 API、自动投放规则、投流 ROI 仅保留预留字段与后续排期，本轮先不实现真实投放或预算相关自动化。

---

## 四、后端依赖图

```text
B1 数据模型与迁移
  ├─ B2 运营大脑 API
  │   ├─ B4 编排引擎适配
  │   └─ F-API 前端切真实接口
  ├─ B3 专家团 API
  │   ├─ B4 子 Agent 调用 Trace
  │   └─ B5 LiteLLM / MCP 工具权限
  └─ B6 多平台账号矩阵与分发

B4 编排引擎适配
  ├─ B5 LangGraph / LiteLLM / MCP
  └─ F5 Trace 可视化

B6 多平台与分发
  └─ F7 账号矩阵任务绑定
```

---

## 五、后端先行任务

### B1：运营大脑数据模型与迁移

**描述：** 新增真实后端领域模型，承载运营大脑任务、结构化 Brief、调度计划、Agent 调用记录、交付物验收、自动化策略。

**验收标准：**
- [x] 新增 `BrainTask`。
- [x] 新增 `TaskBrief`。
- [x] 新增 `OrchestrationPlan`。
- [x] 新增 `AgentInvocation`。
- [x] 新增 `DeliverableAcceptance`。
- [x] 新增 `AutomationPolicy`。
- [x] 新增对应 Alembic 迁移。
- [x] 模型被 `app.models` 导入，测试库 `Base.metadata.create_all` 可创建。
- [x] 在真实 Postgres 上完成 `alembic upgrade head` 验证。
- [ ] 在真实 Postgres 上完成 downgrade/upgrade 往返验证。

**验证：**
- [x] `python -m pytest`
- [x] `python -m ruff check app tests`
- [x] `alembic upgrade head`

**主要文件：**
- `backend/app/models/brain.py`
- `backend/app/models/enums.py`
- `backend/migrations/versions/*_brain_models.py`

---

### B2：运营大脑 API

**描述：** 提供前端运营大脑首页、任务详情、分项验收所需的真实 API。

**验收标准：**
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
- [x] 任务最终验收后可关闭本次任务上下文。
- [ ] 前端 `api/brain.ts` 从 mock adapter 切到真实 API。

**验证：**
- [x] `backend/tests/test_brain_api.py`
- [x] `python -m pytest`
- [x] 前端真实 API 冒烟。

**主要文件：**
- `backend/app/api/brain.py`
- `backend/app/schemas/brain.py`
- `backend/tests/test_brain_api.py`

---

### B3：专家团 API

**描述：** 提供 1 个运营大脑 + 8 个子 Agent 的列表、详情、配置和直接调用接口。直接调用子 Agent 的结果必须回流运营大脑。

**验收标准：**
- [x] `GET /agents`
- [x] `GET /agents/{code}`
- [x] `PATCH /agents/{code}/config`
- [x] `POST /agents/{code}/invoke`
- [x] 管理员可配置模型、兜底模型、自动化级别。
- [x] 普通用户不能修改配置。
- [x] 直接调用子 Agent 会写入 `AgentInvocation` 并关联运营大脑任务。
- [ ] 专家团前端页切到真实 API。

**验证：**
- [x] `backend/tests/test_agents_api.py`
- [x] `python -m pytest`
- [x] 前端专家团真实 API 冒烟。

**主要文件：**
- `backend/app/api/agents.py`
- `backend/app/schemas/brain.py`
- `backend/tests/test_agents_api.py`

---

### B4：编排引擎适配新任务模型

**描述：** 保留现有六阶段 pipeline，把它变成运营大脑可调用的默认参考链路；新增动态计划执行、跳过、并行、返工和部分重跑记录。

**验收标准：**
- [x] `BrainTask.confirm` 后可调用编排引擎。
- [x] 每次子 Agent 调用都写入 `AgentInvocation`。
- [x] `AgentInvocation` 记录输入摘要、输出摘要、模型、token、成本、失败原因、上下游依赖。
- [x] 质量门阻塞、人工确认、打回重跑会影响 `BrainTask.current_focus` 与下一步调度。
- [x] 支持调用链表达：串行、并行、跳过。
- [ ] 支持调用链执行：返工、部分重跑、按 `rerun_scope` 影响上游/下游。

**验证：**
- [x] 新增编排适配测试。
- [x] `backend/tests/test_brain_api.py` 动态计划并行/跳过节点测试通过。
- [x] 旧 `tests/test_orchestrator.py` 继续通过。
- [ ] 全量 `python -m pytest` 需在审批链路恢复后重跑确认。

**主要文件：**
- `backend/app/orchestrator/engine.py`
- `backend/app/orchestrator/pipeline.py`
- `backend/app/api/brain.py`
- `backend/tests/test_brain_orchestration.py`

---

### B5：LiteLLM / LangGraph / MCP 阶段接入

**描述：** 按 SPEC 接入开源组件，但保持业务接口稳定。

**验收标准：**
- [x] LiteLLM 作为 `LLMGateway` 底层 adapter 或代理层，业务调用接口不变。
- [x] LangGraph 作为复杂任务、人机确认、可恢复执行的 adapter，不替换简单 pipeline。
- [x] MCP 先实现内部 `ToolAdapter` 白名单。
- [x] MCP 工具具备 RBAC、参数校验、审计、超时。
- [x] 默认禁止任意命令和任意文件访问工具。

**验证：**
- [x] LLM gateway 单测覆盖 LiteLLM adapter。
- [x] 工具白名单与权限测试通过。
- [ ] `python -m pytest`

**主要文件：**
- `backend/app/llm/*`
- `backend/app/orchestrator/*`
- `backend/app/tools/*`
- `backend/app/mcp/*`

---

### B6：多平台账号矩阵与分发中心

**描述：** 让抖音、小红书、视频号账号矩阵成为任务 Brief 的真实后端约束；分发中心先支持可追踪的半自动流程。

**验收标准：**
- [x] 任务 Brief 可绑定项目、账号组、平台、账号范围。
- [x] 抖音标记真实接入、授权状态、数据回流状态。
- [x] 小红书/视频号标记半自动或手动回填状态。
- [x] 分发动作写入事件与审计记录。
- [x] 后续 OAuth 接入不破坏当前半自动流程。

**验证：**
- [x] 账号矩阵 API 测试。
- [x] 任务 Brief 绑定账号范围测试。
- [ ] `python -m pytest`

**主要文件：**
- `backend/app/models/workspace.py`
- `backend/app/api/accounts.py`
- `backend/app/api/brain.py`
- `backend/app/integrations/*`

---

## 六、前端后置任务

### F-API：前端切真实 API

**描述：** B1-B3 稳定后，把 `frontend/src/api/brain.ts` 和 `frontend/src/api/agents.ts` 从 mock adapter 切换为真实后端 API，并保留 mock fallback 或开发开关。

**验收标准：**
- [x] 运营大脑首页真实调用 `/brain/tasks/draft`、`/confirm`、`/tasks`。
- [x] 专家团页真实调用 `/agents`、`/agents/{code}`、`/agents/{code}/invoke`。
- [x] 分项验收真实调用 `/brain/tasks/{id}/acceptances`、`/accept`、`/rerun`、`/rejudge`、`/close-memory`。
- [ ] 保留开发 mock fallback 或环境变量开关。

**验证：**
- [x] `npm.cmd run lint`
- [x] `npm.cmd run build`
- [x] 前端真实 API 冒烟。

---

### F5：Trace / React Flow 可视化

**依赖：** B4

- [x] 安装并接入 `@xyflow/react`。
- [x] 用真实 `AgentInvocation` 渲染调度图。
- [x] 支持简洁过程 / 完整 Trace 切换。
- [x] 点击节点显示输入、输出、模型、token、成本、失败原因、上下游依赖。
- [x] 补 Playwright 专项用例覆盖缩放、拖拽、节点非重叠断言。
- [x] 修正拖拽断言为校验 React Flow 节点自身坐标，避免 768 宽度页面横向位移误报。
- [ ] 重跑 Playwright 专项用例，通过后关闭 768/1440 缩放/拖拽/不重叠验证。

---

### F7：账号矩阵任务绑定

**依赖：** B6

- [x] 账号矩阵支持项目/赛道/人设/平台/账号组筛选。
- [x] 运营大脑创建任务时可绑定账号范围。
- [x] 显示抖音、小红书、视频号不同接入状态。

---

### F8：复盘闭环、成本、风险整合

**依赖：** B2、B4、B6

- [x] 复盘建议可送入运营大脑生成下一轮 Brief。
- [x] 成本页支持按运营大脑、子 Agent、任务维度查看。
- [x] 风险队列整合质量门、授权过期、模型失败、平台回流失败。

---

### F9：前端浏览器 QA 与测试收口

**依赖：** F-API、F5、F7、F8

- [x] 补核心 Hook/Store Vitest 测试。
- [x] 补核心应用路由 Vitest 冒烟测试。
- [x] 补核心组件 Testing Library 测试。
- [x] 补 Playwright 冒烟。
- [x] 检查 320、768、1024、1440 宽度。
- [x] Console 无运行时错误。

---

## 七、检查点

### P1：后端契约成型

- [x] B1-B3 第一版完成。
- [x] 后端测试通过。
- [x] 后端 lint 通过。
- [x] 真实 Postgres migration upgrade 验证通过。
- [ ] 真实 Postgres migration downgrade/upgrade 往返验证通过。
- [x] 前端 API adapter 切到真实 API。

### P2：后端编排闭环

- [ ] B4 完成。
- [x] 运营大脑确认任务后可触发真实编排。
- [x] 分项验收、重跑、重判会影响下一步调度。
- [x] 旧六阶段 pipeline 测试继续通过。
- [ ] `rerun_scope` 上游/下游/全链路执行语义待补。

### P3：开源组件接入边界

- [x] B5 完成。
- [x] LiteLLM adapter 可用。
- [x] LangGraph adapter 有最小可恢复执行示例。
- [x] MCP 工具白名单、RBAC、审计和超时通过测试。

### P4：多平台与前端收口

- [x] B6 完成。
- [ ] F-API/F5/F7/F8/F9 完成。
- [x] `docker compose up` 后可演示完整端到端流程。

---

## 八、风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 新模型和旧 pipeline 语义重叠 | 高 | 新模型独立落库，旧 pipeline 只作为默认参考链路接入 |
| 前端 mock 与真实 API 字段偏移 | 高 | B2/B3 schema 对齐前端 TypeScript，并用前端切 API 阶段验证 |
| LangGraph/MCP 过早扩大复杂度 | 中 | 先 adapter 与白名单，先测边界再扩能力 |
| 多平台真实授权不稳定 | 中 | 先做发布/数据回流基础；小红书/视频号先半自动记录状态 |
| 投流自动化过早接入 | 高 | 本轮暂缓千川/投流/大额预算自动化，只保留 Agent 和适配器预留 |
| 任务记忆边界模糊 | 高 | 最终验收后只关闭本次上下文，不做长期记忆 |

---

## 九、立即继续顺序

> 2026-07-02 系统重整审查后调整：先做产品收敛切片，再继续后端细节。
> 2026-07-02 优先级再调整：用户确认抖音 / 小红书 / 视频号接入重要性更高；S1 完成后，先做平台接入底座，再回到运营大脑主流程重排。

1. **S1 导航与范围收敛**：隐藏投流、客服等暂缓模块，主导航只保留当前 MVP 可完成能力。
2. **P0 平台接入底座**：统一抖音 / 小红书 / 视频号的账号授权状态、外部账号 ID、接入状态、数据同步状态和数据回流入口。后端已新增 `platform_integrations` / `platform_account_auths` 与 `/platform-integrations` API；前端账号矩阵已可维护平台层 ClientKey、ClientSecret 存储引用、回调地址、安全域名与权限范围。
3. **P1 抖音优先接入**：按抖音开放平台官方文档继续做 OAuth 回调、token 刷新、JS SDK 签名、基础指标回流；发布先做可审计半自动记录，真实发布按开放平台权限推进。当前已完成授权链接、OAuth callback、JS SDK 签名接口与前端“官方授权”入口；下一步接真实 Vault/环境密钥解析和基础指标回流。
4. **P2 小红书、视频号接入**：先做半自动/手动回填与授权状态管理，再按开放平台权限接官方 API。
5. **S2 运营大脑主流程重排**：让“目标 -> Brief -> 调度 -> 交付物 -> 验收 -> 复盘建议 -> 下一轮任务”成为第一屏主线，并使用真实账号接入状态做范围选择。
6. **S3 清理示例数据露出**：复盘看板只保留真实指标与优化建议闭环；示例图隐藏或降级为待接入数据源。
7. **S4 专家团真实感修正**：专家当前任务从真实 `BrainTask` / `AgentInvocation` 聚合，直接调用降级为调试入口。
8. **S5 工程验证回补**：完成产品收敛后再处理 B4 `rerun_scope`、F5 Playwright、Postgres downgrade/upgrade。
9. **投流/千川/大额投放自动化：暂缓，不进入近期实现顺序。**
