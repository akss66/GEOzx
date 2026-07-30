# 运营大脑 V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将运营大脑升级为快速、可审计、账号隔离的混合式 Manager–Worker 运行架构。

**Architecture:** ConversationTurn 是唯一用户消息入口。确定性路由优先处理明确请求，轻量模型仅处理歧义；主 Agent 保持对话控制，Skill Runtime 编排专家与 Tool，Runtime 统一状态、审批、幂等、重试和审计。

**Tech Stack:** FastAPI、SQLAlchemy 2、Pydantic v2、React、TypeScript、TanStack Query、SSE、Pytest、Vitest、Playwright、PostgreSQL。

## Global Constraints

- 不删除或破坏 V2 的历史 Conversation、Turn、SkillRun、Artifact 和审计数据。
- 不执行真实外部发布。
- 每个增量必须先有失败测试，再写最小实现。
- 新能力默认关闭或保持兼容，直到该增量的自动化测试和构建通过。
- 用户界面只展示业务语言；模型、Schema、Tool 原始 JSON 默认折叠。

---

### Task 1: 确定性快速路由

**Files:**
- Modify: `backend/app/orchestrator/capability_router.py`
- Modify: `backend/app/services/turn_execution.py`
- Test: `backend/tests/test_capability_router.py`
- Test: `backend/tests/test_turn_execution.py`

**Interfaces:**
- Produces: `route_deterministic_request(message, *, platform, registry, has_account) -> TurnRouteDecision | None`
- Consumes: `SkillRegistry` 与已发布 Skill code。

- [ ] 写测试：问候、身份和能力询问返回 ANSWER；明确数据查询返回 QUERY；自然语言体检返回 SKILL；复杂歧义请求返回 `None`。
- [ ] 运行 `cd backend && uv run pytest tests/test_capability_router.py tests/test_turn_execution.py -q`，确认新增测试先失败。
- [ ] 实现高置信度、否定词安全的确定性路由，并在 `_route_turn` 调用模型前使用。
- [ ] 断言确定性命中时 `BrainIntelligence.classify_turn` 未被调用。
- [ ] 再次运行定向测试并提交 `feat: add deterministic main-agent routing`。

### Task 2: 路由与回答模型分层

**Files:**
- Modify: `backend/app/models/enums.py`
- Modify: `backend/app/orchestrator/brain_intelligence.py`
- Modify: `backend/app/seed.py`
- Create: `backend/migrations/versions/20260730_0100_main_agent_router_profile.py`
- Test: `backend/tests/test_turn_intelligence.py`
- Test: `backend/tests/test_model_infrastructure.py`

**Interfaces:**
- Produces: 独立路由 workload code `00-router`。
- 保留: `00-decision` 作为主 Agent 回答和综合模型。

- [ ] 写测试：歧义分类调用 `00-router`，回答调用 `00-decision`。
- [ ] 写迁移测试：每个组织获得 Flash 路由配置，供应商/兜底来源与主 Agent 配置兼容。
- [ ] 运行定向测试确认失败。
- [ ] 增加路由 workload、种子和迁移；配置不存在时安全回退到 `00-decision`，不能使生产请求不可用。
- [ ] 运行定向测试并提交 `feat: separate router and answer model profiles`。

### Task 3: Conversation Worker 化与可分类故障

**Files:**
- Modify: `backend/app/api/conversations.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/llm/gateway.py`
- Modify: `backend/app/core/runtime_failures.py`
- Modify: `backend/app/services/agent_runs.py`
- Test: `backend/tests/test_conversation_api.py`
- Test: `backend/tests/test_llm_gateway.py`
- Test: `backend/tests/test_worker.py`

**Interfaces:**
- Conversation API 只负责持久化 Turn/Run 并入队，Worker 负责正式执行。
- `classify_runtime_failure(exc) -> FailureDisposition` 必须保留供应商 HTTP 状态和超时原因。

- [ ] 写测试：提交 Turn 返回已入队 Run，不能在 HTTP 请求线程内执行完整 Runtime。
- [ ] 写测试：409、权限和校验错误不重试；429、5xx、连接和响应超时有界重试。
- [ ] 写测试：LLM Gateway 包装异常后仍可识别原始状态码与 cause。
- [ ] 运行定向测试确认失败。
- [ ] 将 Conversation 初次执行接入现有 durable Worker；保留 SSE 通过 Run/Turn 关联推送。
- [ ] 调整 Gateway 安全异常包装，在不暴露供应商正文的同时保留类型化故障元数据。
- [ ] 运行定向测试并提交 `feat: execute conversation turns through durable workers`。

### Task 4: 统一 Turn/Run/SkillRun/Task 状态机

**Files:**
- Modify: `backend/app/models/conversation.py`
- Modify: `backend/app/models/agent_runtime.py`
- Modify: `backend/app/models/skill_runtime.py`
- Modify: `backend/app/services/agent_runs.py`
- Modify: `backend/app/services/turn_execution.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Create: `backend/app/services/runtime_state.py`
- Create: `backend/migrations/versions/20260730_0200_runtime_state_convergence.py`
- Test: `backend/tests/test_agent_runs.py`
- Test: `backend/tests/test_turn_execution.py`
- Test: `backend/tests/test_account_inspection_skill.py`

**Interfaces:**
- Produces: `close_runtime_state(session, *, scope, status, message, error_code=None)`。
- 状态族固定为 active、paused、terminal；等待审批属于 paused，不得继续显示 running。

- [ ] 写参数化测试：completed、failed、dead_letter、cancelled、waiting_permission 在四类账本中的映射一致。
- [ ] 写测试：失败/取消只产生一条终态用户消息，重放不会重复写。
- [ ] 运行测试确认失败。
- [ ] 增加 Turn 状态和数据库约束，集中收口服务在一个事务中更新所有账本。
- [ ] 将 Worker、Skill 和 operation 的分散状态写入替换为集中收口调用。
- [ ] 运行迁移与定向测试并提交 `fix: converge runtime state across turn ledgers`。

### Task 5: 账号、会话、轮次和成果来源约束

**Files:**
- Modify: `backend/app/models/content.py`
- Modify: `backend/app/models/brain.py`
- Modify: `backend/app/services/conversations.py`
- Modify: `backend/app/services/artifacts.py`
- Modify: `backend/app/orchestrator/agent_harness.py`
- Modify: `backend/app/orchestrator/tool_executor.py`
- Create: `backend/app/orchestrator/runtime_scope.py`
- Create: `backend/migrations/versions/20260730_0300_runtime_scope_constraints.py`
- Test: `backend/tests/test_artifacts_api.py`
- Test: `backend/tests/test_agent_harness.py`
- Test: `backend/tests/test_runtime_tool_executor.py`
- Test: `backend/tests/test_turn_provenance.py`

**Interfaces:**
- Produces: `RuntimeScope(org_id, user_id, account_id, thread_id, turn_id, run_id, skill_run_id=None)`。
- Produces: `validate_runtime_scope(session, scope) -> None`。

- [ ] 写跨用户、跨账号、跨 Thread、跨 Turn、跨 Run 和跨 SkillRun 挂载测试，全部必须拒绝。
- [ ] 写数据库约束测试，直接构造交叉来源 Deliverable/Invocation/ToolCall 也必须失败。
- [ ] 运行测试确认失败。
- [ ] 在专家、Tool、Artifact 写入边界应用完整 scope 校验并增加可行的组合来源约束。
- [ ] 运行迁移与定向测试并提交 `fix: enforce runtime scope and lineage constraints`。

### Task 6: Skill 恢复、专家编排与按需质量门

**Files:**
- Modify: `backend/app/worker.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/orchestrator/skills/account_inspection.py`
- Modify: `backend/app/orchestrator/skills/operating_tasks.py`
- Modify: `backend/app/orchestrator/agent_harness.py`
- Test: `backend/tests/test_skill_runtime_models.py`
- Test: `backend/tests/test_account_inspection_skill.py`
- Test: `backend/tests/test_operating_task_skills.py`

**Interfaces:**
- 恢复使用持久化 `skill_code + skill_version + input_hash`，不得自动升级 Registry 版本。
- SkillDefinition 明确 `expert_codes`、`tool_codes`、`critic_policy`、`artifact_type`。
- 普通 ANSWER/QUERY 不进入 Critic。

- [ ] 写测试：v1 运行中部署 v2 后仍恢复 v1；相同幂等键不同输入必须冲突。
- [ ] 写测试：独立专家可并行；正式成果按策略审核；聊天和查询零 Critic 调用。
- [ ] 写测试：专家失败时主 Agent 不创建冒充专家的正式成果。
- [ ] 运行测试确认失败。
- [ ] 冻结 Skill 恢复参数，实现有界并行和按需质量门。
- [ ] 运行定向测试并提交 `feat: freeze skill recovery and bound expert quality gates`。

### Task 7: 对话删除生命周期和外部副作用幂等

**Files:**
- Modify: `backend/app/services/conversations.py`
- Modify: `backend/app/orchestrator/tool_executor.py`
- Modify: `backend/app/tools/adapter.py`
- Test: `backend/tests/test_conversation_api.py`
- Test: `backend/tests/test_runtime_tool_executor.py`

**Interfaces:**
- 运行中会话删除返回稳定 409；终态后只能永久删除所属用户的对话消息和技术日志。
- 写 Tool 必须声明副作用等级并提供 provider idempotency key；结果不确定时不得自动重放。

- [ ] 写测试：active Run 拒绝删除，正式 Artifact 与 Task 保留并解除会话来源。
- [ ] 写测试：外部成功、本地提交失败时写 Tool 不会二次执行；ambiguous 进入人工处理。
- [ ] 运行测试确认失败。
- [ ] 实现安全删除生命周期和写 Tool exactly-once/outbox 边界。
- [ ] 运行定向测试并提交 `fix: protect active conversations and side effects`。

### Task 8: 前端单一 Turn 投影和专业技术日志

**Files:**
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/components/brain/TurnStream.tsx`
- Modify: `frontend/src/components/brain/TurnArtifact.tsx`
- Modify: `frontend/src/components/brain/ArtifactCenter.tsx`
- Modify: `frontend/src/hooks/useEventStream.ts`
- Test: `frontend/src/pages/BrainHome.test.tsx`
- Test: `frontend/src/components/brain/TurnStream.test.tsx`
- Test: `frontend/src/components/brain/ArtifactCenter.test.tsx`
- Test: `frontend/src/hooks/useEventStream.test.tsx`

**Interfaces:**
- ConversationTurn 是聊天区唯一渲染源。
- SSE 以 `thread_id + turn_id + client_message_id` 合并。
- 同一 Artifact id 同时可由来源 Turn 和成果中心引用。

- [ ] 写测试：pending Turn 与历史 Turn 使用同一布局，不在回答后跳位。
- [ ] 写测试：只显示一个运行状态，delta 实时追加并且 done 不重复正文。
- [ ] 写测试：默认显示参与专家，展开后显示路由、工具、质量门、重试和耗时。
- [ ] 运行前端定向测试确认失败。
- [ ] 实现单一投影与技术日志展示。
- [ ] 运行定向测试、TypeScript 和构建并提交 `fix: unify v3 turn streaming and technical details`。

### Task 9: 性能预算、可观测性与端到端能力矩阵

**Files:**
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Modify: `backend/app/services/turn_execution.py`
- Modify: `backend/app/schemas/conversation.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/e2e/main-agent-v2.spec.ts`
- Create: `backend/tests/test_main_agent_v3_performance.py`
- Create: `docs/runbooks/main-agent-v3-rollout.md`

**Interfaces:**
- 每个 Turn 记录 `route_ms`、`first_token_ms`、`completion_ms`、`total_ms` 和模型调用次数。
- 技术日志只返回脱敏摘要。

- [ ] 写测试：明确普通交流一次模型调用、明确 Skill/QUERY 零路由模型调用。
- [ ] 写测试：确定性路由预算和各阶段耗时字段完整。
- [ ] 增加 10 类用户提示词 E2E 能力矩阵。
- [ ] 实现耗时观测和脱敏投影。
- [ ] 运行后端全量测试、前端全量测试、类型检查、Lint、构建和 Playwright。
- [ ] 进行代码审查并提交 `test: enforce main-agent v3 capability and latency budgets`。
