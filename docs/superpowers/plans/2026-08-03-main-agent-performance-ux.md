# Main Agent Performance and UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将常见回答和查数请求变为低延迟单跳路径，并统一流式状态、成果证据与多平台体验。

**Architecture:** Router 先处理显式 Skill 和确定性意图，再使用轻量分类；普通回答最多一次生成模型调用。前端从统一 Turn phase 投影实时状态，成果证据在业务层聚合、技术层分页，平台能力从当前账号动态加载。

**Tech Stack:** FastAPI、Redis events、LLM adapters、SQLAlchemy telemetry、React、TanStack Query、Vitest、Vite。

## Global Constraints

- 普通回答不得先调用分类模型再调用回答模型。
- 明确查数请求直接调用只读 Tool。
- 一个 Turn 只显示一个实时状态区域。
- 业务界面不得逐条铺开内部证据 ID。
- 前端不得硬编码抖音能力目录。

---

### Task 1: 扩大确定性 Router 覆盖

**Files:**
- Modify: `backend/app/orchestrator/capability_router.py`
- Modify: `backend/app/orchestrator/capability_registry.py`
- Test: `backend/tests/test_capability_router.py`

**Interfaces:**
- Produces: deterministic `answer`、`query`、`skill`、`clarify` 决策和稳定 reason code。

- [ ] **Step 1:** 写表驱动失败测试，覆盖问候、身份、能力、是否有数据、常用指标、明确 Skill 和否定约束。
- [ ] **Step 2:** 运行 Router 测试确认自然表达仍落入模型分类。
- [ ] **Step 3:** 实现归一化意图库和基于 Registry 的 Skill alias 匹配；否定和歧义表达继续 fail closed。
- [ ] **Step 4:** 增加“我现在账号有数据吗”直接 query，禁止模型分类调用。
- [ ] **Step 5:** 运行 Router、Turn 和 Brain Intelligence 测试。
- [ ] **Step 6:** 提交 `perf: expand deterministic main agent routing`。

### Task 2: 单模型普通回答与轻量分类降级

**Files:**
- Modify: `backend/app/services/turn_execution.py`
- Modify: `backend/app/orchestrator/brain_intelligence.py`
- Modify: `backend/app/llm/router.py`
- Test: `backend/tests/test_turn_execution.py`
- Test: `backend/tests/test_brain_intelligence.py`

**Interfaces:**
- Produces: `answer` 路径最多一次生成调用；分类超时返回 safe answer/clarify。

- [ ] **Step 1:** 写失败测试，断言普通回答的 model_call_count 为 1。
- [ ] **Step 2:** 运行测试确认当前模型路由回答发生两次调用。
- [ ] **Step 3:** 合并回答意图判断与生成，或对低风险对话跳过分类；正式动作仍需严格分类。
- [ ] **Step 4:** 给分类调用设置独立低超时和无重试策略，超时不阻塞普通回答。
- [ ] **Step 5:** 运行 Intelligence、Turn 和 API 测试。
- [ ] **Step 6:** 提交 `perf: remove duplicate model calls for answers`。

### Task 3: 完整时延与状态收敛遥测

**Files:**
- Modify: `backend/app/models/conversation.py`
- Create: `backend/migrations/versions/20260803_0300_turn_latency_metrics.py`
- Modify: `backend/app/services/turn_execution.py`
- Modify: `backend/app/services/runtime_events.py`
- Test: `backend/tests/test_turn_execution.py`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces: 每个终态 Turn 的 route_ms、first_status_ms、first_token_ms、total_ms、model_call_count、tool_call_count。

- [ ] **Step 1:** 写失败测试，要求所有 completed/failed/blocked Turn 都有 total_ms，流式回答有 first_token_ms。
- [ ] **Step 2:** 运行并确认当前部分记录为空。
- [ ] **Step 3:** 在单一 finally 收口中写终态指标，确保异常和恢复路径也执行。
- [ ] **Step 4:** 增加 Run/Turn 终态不一致计数和 Skill 阶段超时事件。
- [ ] **Step 5:** 运行迁移、Turn、Worker 和事件测试。
- [ ] **Step 6:** 提交 `feat: complete main agent latency telemetry`。

### Task 4: 单一流式状态投影

**Files:**
- Modify: `backend/app/services/runtime_events.py`
- Modify: `frontend/src/components/brain/TurnStream.tsx`
- Modify: `frontend/src/components/brain/TurnStream.test.tsx`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Test: `frontend/src/pages/BrainHome.test.tsx`

**Interfaces:**
- Produces: `TurnPhase = understanding | reading_data | consulting_experts | quality_review | waiting_approval | composing_artifact | completed | failed`。

- [ ] **Step 1:** 写失败测试，确保同一 Turn 不同时显示“思考中”和“正在思考”。
- [ ] **Step 2:** 运行前端测试确认失败场景。
- [ ] **Step 3:** 后端只发布规范化 phase 事件，前端用一个状态组件覆盖当前阶段。
- [ ] **Step 4:** 保留文本 token 流；Skill 只输出阶段事件和最终成果，不伪造 token。
- [ ] **Step 5:** 运行 TurnStream、BrainHome 和 API 事件测试。
- [ ] **Step 6:** 提交 `fix: unify main agent streaming status`。

### Task 5: 业务证据聚合与技术日志分页

**Files:**
- Modify: `backend/app/services/artifacts.py`
- Modify: `backend/app/schemas/artifacts.py`
- Modify: `frontend/src/components/brain/ArtifactCard.tsx`
- Modify: `frontend/src/components/brain/ArtifactCard.test.tsx`
- Modify: `frontend/src/styles/brain-v2.css`
- Test: `backend/tests/test_artifacts_api.py`

**Interfaces:**
- Produces: `evidence_summary[]` 和分页 `technical_evidence`；业务摘要按 kind/source/period/metric_count 聚合。

- [ ] **Step 1:** 写失败测试，将 79 个 field_observation 期望为一条聚合摘要。
- [ ] **Step 2:** 运行后端与前端测试，确认当前逐条渲染。
- [ ] **Step 3:** 后端聚合证据摘要，保留原始引用用于审计和分页 API。
- [ ] **Step 4:** ArtifactCard 默认显示摘要和参与专家；技术日志展开后分页加载原始 ID。
- [ ] **Step 5:** 运行 Artifact API、组件和可访问性测试。
- [ ] **Step 6:** 提交 `fix: summarize artifact evidence for operators`。

### Task 6: 动态平台能力与输入框体验

**Files:**
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.test.tsx`
- Modify: `frontend/src/api/brain.ts`

**Interfaces:**
- Consumes: `activeAccount.platform` 和 Capability Registry availability。
- Produces: 当前平台能力菜单和不可用原因。

- [ ] **Step 1:** 写失败测试，切换小红书/视频号账号后请求对应平台能力，不再请求 `douyin`。
- [ ] **Step 2:** 运行测试确认硬编码失败。
- [ ] **Step 3:** Query key 和 API 参数使用 `activeAccount.platform`；切换账号清理旧能力和草稿附件。
- [ ] **Step 4:** `coming_soon` 只显示说明，`needs_connection` 提供连接入口，`available` 才能执行。
- [ ] **Step 5:** 修复测试环境 textarea NaN height 警告并验证键盘、焦点和禁用状态。
- [ ] **Step 6:** 运行 BrainComposer、BrainHome、API 测试和 TypeScript 检查。
- [ ] **Step 7:** 提交 `fix: load main agent capabilities by platform`。

### Task 7: 前端加载性能与分包

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/vite.config.ts`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Test: `frontend/src/pages/BrainHome.test.tsx`

**Interfaces:**
- Produces: 路由级懒加载和稳定 vendor chunks。

- [ ] **Step 1:** 记录当前构建中 antd/charts 超过 1.1MB 的基线。
- [ ] **Step 2:** 对非首屏图表、复盘和管理页面使用 `React.lazy`，不延迟主 Agent 基础输入框。
- [ ] **Step 3:** 配置稳定 manualChunks，避免主 Agent 首屏加载图表包。
- [ ] **Step 4:** 运行生产构建，断言主 Agent 初始入口不依赖 charts chunk。
- [ ] **Step 5:** 运行前端全量测试和构建。
- [ ] **Step 6:** 提交 `perf: reduce main agent initial bundle`。

### Task 8: 性能门与生产灰度验收

**Files:**
- Create: `backend/tests/test_main_agent_performance_contract.py`
- Modify: `docs/runbooks/main-agent-v3-rollout.md`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: 可重复的 Router/Tool 本地性能契约和生产 P95 查询。

- [ ] **Step 1:** 添加不依赖外网的 deterministic route、query Tool 性能测试。
- [ ] **Step 2:** CI 执行后端主 Agent 测试、前端主 Agent 测试、类型检查和生产构建。
- [ ] **Step 3:** Runbook 写入首字、路由、查询、Skill 阶段更新和终态一致性 SQL。
- [ ] **Step 4:** 内部账号灰度 24 小时，要求错误率、重复消息和状态错位为零，P95 达到设计预算。
- [ ] **Step 5:** 未达到预算时关闭对应开关并保留遥测，不扩大灰度。
- [ ] **Step 6:** 提交 `chore: enforce main agent performance gates`。
