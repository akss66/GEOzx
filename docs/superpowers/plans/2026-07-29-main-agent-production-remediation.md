# Main Agent Production Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 关闭运营大脑双运行时入口，并让问答、数据查询、体检、选题、脚本、发布准备和复盘全部通过 Turn 级隔离的公开能力契约执行。

**Architecture:** 前端所有发送统一创建/复用 ConversationThread 并提交 ConversationTurn。后端以 Skill Registry 作为唯一公开能力目录，直接回答和查询不创建 BrainTask，正式能力由有界 SkillRun 调用指定专家并产出固定类型 Artifact；旧 AI COO 路径只保留历史兼容。

**Tech Stack:** FastAPI、SQLAlchemy 2、Pydantic v2、React、TypeScript、TanStack Query、Vitest、Pytest、PostgreSQL、SSE。

## Global Constraints

- 不删除旧 `/brain/messages` 和历史账本。
- 不增加新的一级导航或外部依赖。
- 不执行真实发布。
- 每条新消息只能拥有一个 Thread、Turn、Run 和当前目标。
- 未经人工确认不得进入外部发布动作。
- 默认用户界面不得展示 Tool 原始 JSON、内部 Schema 或模型协议。

---

### Task 1: 统一 Conversation 发送入口

**Files:**
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/api/brain.ts`
- Test: `frontend/src/pages/BrainHome.test.tsx`
- Test: `frontend/e2e/main-agent-v2.spec.ts`

**Interfaces:**
- Consumes: `createConversation({ account_id })`、`sendConversationTurn(threadId, input)`。
- Produces: `ensureConversationThread(accountId): Promise<number>`，供普通发送和快捷能力共同使用。

- [ ] **Step 1: 写失败测试**

增加测试：点击“新对话”后发送普通消息，断言调用 `createConversation` 和
`sendConversationTurn`，且不调用 `sendBrainMessage`；断言待发送消息与历史 Turn 使用
相同容器和对齐类名。

- [ ] **Step 2: 验证测试按预期失败**

Run: `cd frontend && pnpm test -- BrainHome.test.tsx`

Expected: FAIL，普通消息仍调用 `sendBrainMessage`。

- [ ] **Step 3: 实现统一入口**

将 `startWorkflow` 改为异步确保 Thread 后调用 `conversationTurnMutation.mutateAsync`。
“新对话”立即创建并选中新 Thread；旧 task 状态仅用于历史兼容展示。

- [ ] **Step 4: 验证通过**

Run: `cd frontend && pnpm test -- BrainHome.test.tsx`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/BrainHome.tsx frontend/src/api/brain.ts frontend/src/pages/BrainHome.test.tsx frontend/e2e/main-agent-v2.spec.ts
git commit -m "fix: route all brain messages through conversation turns"
```

### Task 2: 主 Agent 模型回答与业务化数据摘要

**Files:**
- Modify: `backend/app/orchestrator/brain_intelligence.py`
- Modify: `backend/app/services/turn_execution.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Modify: `backend/app/prompts/main-agent/conversation/v1.md`
- Test: `backend/tests/test_turn_execution.py`
- Test: `backend/tests/test_turn_intelligence.py`

**Interfaces:**
- Produces: `BrainIntelligence.answer_turn(session, org_id, message, operating_context, history, observer) -> str`。
- Produces: `format_account_data_summary(data: dict, requested_message: str) -> str`。

- [ ] **Step 1: 写失败测试**

覆盖普通能力询问由模型回答、回答上下文只包含公开 Skill；覆盖数据查询回复包含周期和
可用指标但不包含 `evidence_refs`、Python dict 或 JSON 协议。

- [ ] **Step 2: 验证测试按预期失败**

Run: `cd backend && uv run pytest tests/test_turn_execution.py tests/test_turn_intelligence.py -q`

Expected: FAIL，普通回复仍是固定话术，查询仍是固定“已读取”。

- [ ] **Step 3: 实现回答和摘要**

为普通回答绑定 `main-agent.conversation` Prompt、账号业务上下文和当前 Thread 历史。
查询使用确定性格式器输出已取得指标、周期和缺失范围；Tool 原始结果只保留在
SkillRun/ToolCall 账本。

- [ ] **Step 4: 验证通过**

Run: `cd backend && uv run pytest tests/test_turn_execution.py tests/test_turn_intelligence.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/orchestrator/brain_intelligence.py backend/app/services/turn_execution.py backend/app/orchestrator/brain_runtime.py backend/app/prompts/main-agent/conversation/v1.md backend/tests/test_turn_execution.py backend/tests/test_turn_intelligence.py
git commit -m "fix: produce grounded turn answers and data summaries"
```

### Task 3: 注册四个有界运营 Skill

**Files:**
- Create: `backend/app/orchestrator/skills/operating_tasks.py`
- Modify: `backend/app/orchestrator/skills/registry.py`
- Modify: `backend/app/orchestrator/skills/public_catalog.py`
- Modify: `backend/app/schemas/deliverable.py`
- Modify: `backend/app/prompts/main-agent/intent/v2.md`
- Test: `backend/tests/test_skill_registry.py`
- Test: `backend/tests/test_turn_intelligence.py`

**Interfaces:**
- Produces Skill codes: `topic_planning`、`script_generation`、
  `publishing_preparation`、`performance_review`。
- 每个 `SkillDefinition` 固定 expert_codes、artifact_type、input_model 和 output_model。

- [ ] **Step 1: 写失败测试**

断言四个 Skill 在抖音 composer 目录公开；用典型提示词验证分类器可返回对应 code；
断言每个输出 Schema 拒绝错误成果类型和缺少必要业务字段的数据。

- [ ] **Step 2: 验证测试按预期失败**

Run: `cd backend && uv run pytest tests/test_skill_registry.py tests/test_turn_intelligence.py -q`

Expected: FAIL，Registry 当前仅含 `account_inspection`。

- [ ] **Step 3: 实现 Skill 契约**

增加四个定义和强类型输出；更新公开目录和意图 Prompt，明确用户的禁止项不能被转换为
额外策略步骤。

- [ ] **Step 4: 验证通过**

Run: `cd backend && uv run pytest tests/test_skill_registry.py tests/test_turn_intelligence.py -q`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/orchestrator/skills/operating_tasks.py backend/app/orchestrator/skills/registry.py backend/app/orchestrator/skills/public_catalog.py backend/app/schemas/deliverable.py backend/app/prompts/main-agent/intent/v2.md backend/tests/test_skill_registry.py backend/tests/test_turn_intelligence.py
git commit -m "feat: register bounded operating skills"
```

### Task 4: 执行专家 Skill 并原子收口成果

**Files:**
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/services/turn_execution.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Test: `backend/tests/test_account_inspection_skill.py`
- Create: `backend/tests/test_operating_task_skills.py`
- Modify: `backend/tests/test_turn_execution.py`

**Interfaces:**
- `SkillRuntime.execute(...)` 支持 Registry 中五个正式 Skill。
- `SkillExecutionResult` 返回唯一 `artifact_id`、`artifact_type`、参与专家和稳定终态。

- [ ] **Step 1: 写失败测试**

分别执行选题、脚本、发布准备和复盘，断言每个 Turn 只产生一个对应类型成果、一个
SkillRun、一个 BrainTask，且 `StrategyPlan` 数量为零；断言专家、工具和成果的
thread/turn/run/skill_run 归属一致；断言失败时所有运行对象退出 running。

- [ ] **Step 2: 验证测试按预期失败**

Run: `cd backend && uv run pytest tests/test_operating_task_skills.py tests/test_turn_execution.py -q`

Expected: FAIL，四个 Skill 尚无执行器。

- [ ] **Step 3: 实现最小专家执行图**

选题和脚本调用编导专家；发布准备和复盘调用运营专家。使用 Tool 结果作为只读证据，
通过输出 Schema 校验后创建唯一 Deliverable。发布准备额外创建
`publish_package_prepare` 待确认 ToolCall，但不调用平台发布。

- [ ] **Step 4: 修复账号体检状态收口**

体检完成、阻塞和失败都同步更新 SkillRun、AgentRun、BrainTask、Turn 和成果引用；
不合格重试保持同一个 SkillRun 和成果类型。

- [ ] **Step 5: 验证通过**

Run: `cd backend && uv run pytest tests/test_account_inspection_skill.py tests/test_operating_task_skills.py tests/test_turn_execution.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/orchestrator/skill_runtime.py backend/app/services/turn_execution.py backend/app/orchestrator/brain_runtime.py backend/tests/test_account_inspection_skill.py backend/tests/test_operating_task_skills.py backend/tests/test_turn_execution.py
git commit -m "feat: execute bounded skills with turn-owned artifacts"
```

### Task 5: 前端能力、证据与技术详情

**Files:**
- Modify: `frontend/src/components/brain/CapabilityLauncher.tsx`
- Modify: `frontend/src/components/brain/TurnStream.tsx`
- Modify: `frontend/src/components/brain/TurnArtifact.tsx`
- Modify: `frontend/src/components/brain/ArtifactCard.tsx`
- Modify: `frontend/src/types.ts`
- Test: `frontend/src/components/brain/CapabilityLauncher.test.tsx`
- Test: `frontend/src/components/brain/TurnStream.test.tsx`
- Test: `frontend/src/components/brain/ArtifactCard.test.tsx`

**Interfaces:**
- Consumes Registry 返回的公开 Skill，不维护硬编码可执行能力。
- Turn projection 默认呈现业务阶段、专家和成果；technical details 展示本 Turn 审计摘要。

- [ ] **Step 1: 写失败测试**

覆盖五个快捷能力、中文业务字段、参与专家、证据周期、质量信息和默认折叠技术日志；
断言默认 DOM 不出现 Tool 原始 `result` 和内部 Schema 键。

- [ ] **Step 2: 验证测试按预期失败**

Run: `cd frontend && pnpm test -- CapabilityLauncher.test.tsx TurnStream.test.tsx ArtifactCard.test.tsx`

Expected: FAIL，目录和详情尚不完整。

- [ ] **Step 3: 实现展示**

由 Registry 动态渲染快捷能力；按成果类型展示中文业务结构；技术日志增加本 Turn 的
路由、Run、Skill、专家、工具、质量和耗时摘要。

- [ ] **Step 4: 验证通过**

Run: `cd frontend && pnpm test -- CapabilityLauncher.test.tsx TurnStream.test.tsx ArtifactCard.test.tsx`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/brain/CapabilityLauncher.tsx frontend/src/components/brain/TurnStream.tsx frontend/src/components/brain/TurnArtifact.tsx frontend/src/components/brain/ArtifactCard.tsx frontend/src/types.ts frontend/src/components/brain/CapabilityLauncher.test.tsx frontend/src/components/brain/TurnStream.test.tsx frontend/src/components/brain/ArtifactCard.test.tsx
git commit -m "feat: present operating skills and turn evidence"
```

### Task 6: 全量验证与生产回归

**Files:**
- Modify: `frontend/e2e/main-agent-v2.spec.ts`
- Modify: `docs/runbooks/main-agent-v2-rollout.md`
- Modify: `docs/HANDOFF_MAIN_AGENT_V2_2026-07-28.md`

**Interfaces:**
- 验证本设计第 7 节九个生产场景。

- [ ] **Step 1: 完成本地验证**

```bash
cd backend
uv run pytest
uv run ruff check .

cd ../frontend
pnpm test
pnpm lint
pnpm build
```

Expected: 全部通过，无新增 warning 或 error。

- [ ] **Step 2: 执行容器回归**

```bash
docker compose up -d --build
docker compose ps
```

对同一个测试账号按验收提示词运行，核对每个 Turn、SkillRun、成果和审批来源。

- [ ] **Step 3: 更新发布记录并提交**

```bash
git add frontend/e2e/main-agent-v2.spec.ts docs/runbooks/main-agent-v2-rollout.md docs/HANDOFF_MAIN_AGENT_V2_2026-07-28.md
git commit -m "test: cover production main agent operating loop"
```

- [ ] **Step 4: 部署后生产验收**

部署当前提交，确认服务健康后重新执行九个场景。若任一场景出现策略越权、旧任务污染、
原始协议泄漏或状态未收口，立即停止验收并回滚到部署前镜像。

