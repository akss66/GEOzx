# Production Agent Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将运营大脑升级为可持久恢复、可并发、可审计、可灰度的生产级 Agent Runtime，并落地运行时记忆与审核式长期知识。

**Architecture:** PostgreSQL 保存业务运行与 LangGraph checkpoint，Redis/ARQ 只负责投递。主 Agent 在 worker 中运行受控开放式 ReAct，所有专家/工具/MCP 经过统一能力与权限边界；运行时自动形成线程记忆，长期知识只经建议审核进入正式库。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy Async、PostgreSQL 16、Redis 7、ARQ、LangGraph 1.2、AsyncPostgresSaver、Pydantic 2、pytest、React/Vite/Vitest。

## Global Constraints

- 新运行时默认关闭，只能通过组织/账号灰度开关启用。
- 移动端不在本计划范围内。
- 外部发布、投流、删除和资金动作始终需要人工确认。
- 不复制 Grok Build 或 Claude Code 源码，只实现已确认的产品与架构原则。
- 每一任务严格执行 RED -> GREEN -> REFACTOR，并在独立验收点运行相关测试。

---

### Task 1: 安全范围、并发上下文与输入幂等基础

**Files:**
- Modify: `backend/app/orchestrator/engine.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Modify: `backend/app/api/brain.py`
- Test: `backend/tests/test_orchestrator.py`
- Test: `backend/tests/test_brain_runtime.py`
- Test: `backend/tests/test_brain_api.py`

**Interfaces:**
- Produces: `_knowledge(session, *, org_id, client_id, project_id)`，只返回当前客户级与当前项目级 active 知识。
- Produces: `bind_runtime_session(session)` 基于 `ContextVar` 的调用级 session 绑定。
- Produces: `client_message_id` 重放返回原运行，不重复写用户消息或启动 Agent。

- [ ] 写跨客户、跨项目、归档知识不可注入的失败测试。
- [ ] 运行定向测试并确认旧 `_knowledge(org_id)` 失败。
- [ ] 从 `ContentItem.project` 解析 `client_id/project_id`，复用 `list_agent_knowledge()`。
- [ ] 写两个并发 runtime 各自读取独立 session 的失败测试。
- [ ] 以 `ContextVar` 替换 `_session_from_state._active_session`，用 token 在 `finally` 中恢复。
- [ ] 写重复 `client_message_id` 的 API 失败测试。
- [ ] 增加数据库级或事务级幂等检查，重复请求返回同一个 task/run 投影。
- [ ] 运行 `pytest tests/test_orchestrator.py tests/test_brain_runtime.py tests/test_brain_api.py -q` 与 Ruff。

### Task 2: AgentRun、租约与后台执行

**Files:**
- Create: `backend/app/models/agent_runtime.py`
- Create: `backend/app/services/agent_runs.py`
- Create: `backend/app/orchestrator/runtime_worker.py`
- Create: `backend/migrations/versions/20260721_0100_agent_runs.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/api/brain.py`
- Modify: `backend/app/schemas/brain.py`
- Test: `backend/tests/test_agent_runs.py`
- Test: `backend/tests/test_runtime_worker.py`

**Interfaces:**
- Produces: `AgentRun` 状态机 `queued/running/waiting/retry_scheduled/completed/failed/cancelled/dead_letter`。
- Produces: `enqueue_agent_run(run_id)` 与 `execute_agent_run(ctx, run_id)`。
- Produces: `claim_run(session, run_id, worker_id, lease_seconds)` 原子租约。

- [ ] 写消息事务只创建一次 run 的失败测试。
- [ ] 添加模型、唯一约束、迁移和状态转换服务。
- [ ] 写重复投递只有一个 worker 获得租约的失败测试。
- [ ] 实现租约、心跳、取消检查、错误分类与指数退避。
- [ ] 将 `/brain/messages` 改为快速返回 queued/running 投影，移除请求内 Agent loop。
- [ ] 注册 ARQ worker 函数，数据库状态作为事实来源。
- [ ] 增加扫描过期租约和可重投运行的恢复任务。
- [ ] 运行模型、API、worker、迁移和故障注入测试。

### Task 3: LangGraph PostgreSQL checkpointer 与原生 interrupt

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/app/orchestrator/checkpointing.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Modify: `backend/app/worker.py`
- Modify: `.env.example`
- Test: `backend/tests/test_checkpointing.py`
- Test: `backend/tests/test_brain_runtime_persistence.py`

**Interfaces:**
- Produces: `build_checkpointer()`，生产返回 `AsyncPostgresSaver`，测试可注入 `InMemorySaver`。
- Produces: 单一 compiled graph，使用稳定 `thread_id` 与 `Command(resume=...)`。

- [ ] 写进程重建后从同一 thread 恢复的集成失败测试。
- [ ] 添加 `langgraph-checkpoint-postgres` 与 psycopg 依赖，并锁定兼容范围。
- [ ] 实现 SQLAlchemy URL 到 psycopg URL 的安全转换、`setup()` 和生命周期。
- [ ] 使用严格 msgpack；存在 `LANGGRAPH_AES_KEY` 时启用加密 serializer。
- [ ] 将权限和方案暂停改为 `interrupt()`；删除独立 resume graph 分叉。
- [ ] 验证 interrupt 前副作用重复执行时保持幂等。
- [ ] 增加 checkpoint 保留与关闭 thread 清理入口。

### Task 4: 完整受控 ReAct 与统一工具边界

**Files:**
- Modify: `backend/app/schemas/brain.py`
- Modify: `backend/app/orchestrator/brain_intelligence.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Modify: `backend/app/orchestrator/capability_registry.py`
- Create: `backend/app/orchestrator/tool_executor.py`
- Test: `backend/tests/test_brain_react_loop.py`
- Test: `backend/tests/test_tool_executor.py`

**Interfaces:**
- Consumes: `AgentRun`、persistent graph。
- Produces: 全动作 `respond/ask_user/dispatch_experts/call_tools/request_decision/request_permission/finish`。
- Produces: `ToolExecutor.execute(call, scope, permission)`，未来 MCP 使用同一入口。

- [ ] 为每种动作写路由失败测试，未知动作必须显式失败。
- [ ] 允许同一专家在 `purpose/evidence` 变化时重复派发，禁止无新信息循环。
- [ ] 增加轮次、单专家次数、Token、成本、时间和工具次数预算。
- [ ] 统一工具 schema、范围校验、权限判断、幂等和结果脱敏。
- [ ] 高风险工具通过 interrupt；只读低风险工具可自动执行。
- [ ] 写预算耗尽、重复专家、提示注入工具参数和跨账号工具调用测试。

### Task 5: Prompt Registry 与专家 Harness

**Files:**
- Create: `backend/app/prompts/manifest.py`
- Create: `backend/app/prompts/main_agent/v1.md`
- Create: `backend/app/prompts/memory_compactor/v1.md`
- Create: `backend/app/prompts/knowledge_extractor/v1.md`
- Modify: `backend/app/prompts/agents/*.md`
- Modify: `backend/app/agents/base.py`
- Modify: `backend/app/llm/gateway.py`
- Test: `backend/tests/test_prompt_registry.py`
- Test: `backend/tests/test_agent_harness.py`

**Interfaces:**
- Produces: `PromptSpec(id, version, content_hash, schema_version)`。
- Produces: `AgentHarness.run()` 的解析、schema 校验、业务校验和一次有界修复。

- [x] 写缺失 Prompt、hash 变化、未知版本和草稿标记阻断测试。
- [x] 将主 Agent 内联 Prompt 迁移为不可变版本文件。
- [x] 重写八位专家 Prompt，移除 draft/TODO，声明边界、证据和输出契约。
- [x] 使用结构化输出，不再通过宽泛正则猜测 JSON。
- [x] 每次模型调用记录 Prompt 元数据、scope、预算与 trace ID。
- [x] 建立主 Agent 与八位专家最小契约评测集。

### Task 6: 运行时记忆

**Files:**
- Create: `backend/app/models/memory.py`
- Create: `backend/app/services/runtime_memory.py`
- Create: `backend/migrations/versions/20260721_0400_runtime_memory.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Test: `backend/tests/test_runtime_memory.py`

**Interfaces:**
- Produces: `RuntimeMemory` 类型 `summary/decision/observation/todo/scope_snapshot`。
- Produces: `build_runtime_context(thread_id, budget)`，保留最近原文和压缩历史。

- [x] 写长会话不会简单截断关键决定的失败测试。
- [x] 添加记忆模型、范围字段、修订号和来源事件游标。
- [x] 实现预算化上下文构建：压缩记忆优先，最近未压缩事件补齐。
- [x] 实现压缩摘要、严格 JSON 校验和失败保留上一版降级。
- [x] 记录 compact 事件与摘要来源，支持审计和重建。
- [x] 写跨 thread、跨客户、重复压缩和摘要失败降级测试。
- [ ] 后台化自动写入：同步请求链路中默认关闭，后续由 worker/恢复投影触发。

### Task 7: 自动知识候选、去重与冲突治理

**Files:**
- Modify: `backend/app/models/knowledge.py`
- Create: `backend/app/services/knowledge_extraction.py`
- Create: `backend/app/services/knowledge_retrieval.py`
- Create: `backend/migrations/versions/20260721_0300_knowledge_governance.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/api/knowledge_suggestions.py`
- Test: `backend/tests/test_knowledge_extraction.py`
- Test: `backend/tests/test_knowledge_retrieval.py`

**Interfaces:**
- Produces: `extract_knowledge_candidates(run_id)` 后台任务。
- Produces: `fingerprint/similarity/conflict_status/confidence/evidence/expires_at`。
- Produces: 范围优先的 `retrieve_knowledge(scope, query, limit)`。

- [ ] 写未经验收成果不提取、重复候选不重复创建的失败测试。
- [ ] 扩展建议治理字段和唯一约束。
- [ ] 从已验收成果、明确决定和有证据观察中提取候选。
- [ ] 实现确定性指纹、候选去重、冲突/替代标记。
- [ ] 审核通过创建新版本或替代旧条目，拒绝不污染正式库。
- [ ] 所有检索先做 org/client/project/account 范围过滤并记录 citation。
- [ ] 语义检索用 feature flag 隔离；关闭时使用关键词/标签相关性。

### Task 8: 可观测性、恢复与安全加固

**Files:**
- Create: `backend/app/core/agent_telemetry.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/orchestrator/runtime_worker.py`
- Modify: `backend/app/api/health.py`
- Create: `docs/runbooks/agent-runtime.md`
- Test: `backend/tests/test_agent_telemetry.py`
- Test: `backend/tests/test_runtime_failure_recovery.py`

**Interfaces:**
- Produces: 结构化事件和 bounded-cardinality RED/USE 指标。
- Produces: `/health/ready` 的 worker/checkpointer 诊断摘要。

- [ ] 定义并测试 request/run/thread/task 关联字段。
- [ ] 对模型、专家、工具、队列和 checkpoint 增加持续时间与错误分类。
- [ ] 确保日志不含 API Key、Token、完整 Prompt 或完整模型正文。
- [ ] 写 worker 崩溃、租约过期、限流、认证失败、取消和 dead-letter 测试。
- [ ] 编写告警阈值、排障查询、恢复与回滚 runbook。

### Task 9: 前端运行状态与恢复投影

**Files:**
- Modify: `frontend/src/api/brain.ts`
- Modify: `frontend/src/hooks/useEventStream.ts`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.tsx`
- Test: `frontend/src/pages/BrainHome.test.tsx`
- Test: `frontend/src/hooks/useEventStream.test.tsx`

**Interfaces:**
- Consumes: queued/running/waiting/retry/dead-letter/cancelled runtime projection。
- Produces: 断线恢复、跨刷新恢复、停止、重试和人工确认的稳定桌面体验。

- [ ] API 快速返回后显示排队/思考状态，不阻塞输入发送。
- [ ] WebSocket 重连按 event cursor 补齐，不重复渲染 token/event。
- [ ] 显示可操作的暂停、重试、恢复和失败原因，不暴露内部堆栈。
- [ ] 权限与方案选择继续使用已确认的输入器原位交互。
- [ ] 运行前端测试、生产构建和桌面浏览器验收。

### Task 10: 评测、灰度与真实抖音账号验证

**Files:**
- Create: `backend/evals/agent_runtime/*.json`
- Create: `backend/tests/evals/test_agent_runtime_evals.py`
- Modify: `.env.example`
- Modify: `docker-compose.prod.yml`
- Modify: `tasks/current.md`

**Interfaces:**
- Produces: 可重复的路由、权限、范围、恢复和知识质量评测。
- Produces: 组织/账号级灰度开关与回滚步骤。

- [ ] 覆盖问候、澄清、分析、工作流、外部动作和提示注入场景。
- [ ] 完成后端全量 pytest、Ruff、迁移单 head、前端全量测试与构建。
- [ ] 在本地执行进程崩溃、Redis 暂停、数据库短断和模型限流演练。
- [ ] 经用户批准后部署，但仅对白名单管理员和一个抖音测试账号启用 V2。
- [ ] 先验证只读账号分析和数据回流，再验证内部成果与发布包准备。
- [ ] 外部动作保持人工确认，记录生产冒烟、指标、回滚结果和用户验收。
