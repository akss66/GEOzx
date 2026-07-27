# AI COO Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏现有 BrainTask、AgentInvocation、AgentToolCall、Deliverable、Event 和权限体系的前提下，把主 Agent 增量升级为可审计、可观测、可复盘和可学习的 AI 运营负责人。

**Architecture:** 现有 `BrainRuntimeGraph` 继续承载 durable execution，新增 `coo_v1` 运行模式和专用的结构化状态、运营语义账本与决策服务。每个阶段只保存业务结论、证据引用和选择理由，不保存模型私有思维链；所有经验必须由真实数据或人工确认后才能转为已验证记忆。

**Tech Stack:** FastAPI、SQLAlchemy 2、Alembic、Pydantic v2、LangGraph、PostgreSQL/SQLite、React、TypeScript、Vitest。

## Global Constraints

- 保持已有 API、数据库模型、权限体系向后兼容。
- 不删除已有能力，不使用 Mock 数据。
- 所有新增能力必须基于真实 Runtime 和持久化账本。
- 简单问候或普通问答不创建运营任务、不调用专家。
- 所有诊断必须引用数据来源；无证据时明确返回“数据不足”。
- Critic 自动改进最多两轮。
- 投稿、删除、投流和未来 MCP 高风险动作必须经过现有权限门。
- 主 Agent 只负责决策、调度、监督和汇总，不替代专家直接交付专业成果。

---

### Task 1: AI COO 数据契约与运营语义账本

**Files:**
- Create: `backend/app/models/ai_coo.py`
- Create: `backend/app/schemas/ai_coo.py`
- Create: `backend/migrations/versions/20260727_0300_ai_coo_runtime.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/brain.py`
- Test: `backend/tests/test_ai_coo_models.py`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces: `COORuntimeState`, `EvidenceRef`, `StrategyPlan`, `DecisionTrace`, `ExperienceMemory`, `ReflectionRecord`, `AgentQualityScore`.
- Consumes: existing organization, client, project, account, task, run, invocation and deliverable identifiers.

- [ ] Write model/schema tests for all five tables, evidence validation and old-task compatibility.
- [ ] Run focused tests and verify they fail before implementation.
- [ ] Add additive ORM models, optional BrainTask relationships and Alembic migration.
- [ ] Add strict Pydantic contracts; reject unsupported evidence source types and invalid confidence/score values.
- [ ] Run model, migration and metadata tests.
- [ ] Commit the independently deployable schema slice.

### Task 2: Read APIs and evidence-backed situation service

**Files:**
- Create: `backend/app/services/ai_coo.py`
- Modify: `backend/app/api/brain.py`
- Modify: `backend/app/api/accounts.py`
- Modify: `backend/app/main.py` only if a new router is required
- Test: `backend/tests/test_ai_coo_api.py`

**Interfaces:**
- Produces: `get_account_situation`, `get_task_strategy`, `list_task_decisions`, `list_task_quality_scores`, `get_task_reflection`.
- Consumes: account data center records, metric snapshots, imported content records and knowledge citations.

- [ ] Write failing authorization, organization-scope and no-evidence tests.
- [ ] Implement evidence collection using persisted data only.
- [ ] Implement task/account read endpoints with stable empty states instead of fabricated zeros.
- [ ] Verify cross-organization IDs return not found.
- [ ] Run focused API tests and commit.

### Task 3: Goal, situation, strategy and decision nodes

**Files:**
- Create: `backend/app/orchestrator/coo_runtime.py`
- Create: `backend/app/prompts/coo.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Modify: `backend/app/orchestrator/agent_harness.py`
- Modify: `backend/app/api/brain.py`
- Test: `backend/tests/test_coo_runtime.py`
- Test: `backend/tests/test_brain_runtime.py`

**Interfaces:**
- Produces: `build_coo_graph()`, nodes `goal_understanding`, `context_resolution`, `situation_awareness`, `strategy_planning`, `task_planning`.
- Consumes: `COORuntimeState`, existing harness/model routing, RuntimeMemory and Event ledger.

- [ ] Write failing graph-routing tests for greeting, data-insufficient and operational-goal paths.
- [ ] Add versioned prompt contracts with structured output validation.
- [ ] Add `coo_v1` runtime selection while preserving legacy/langgraph tasks.
- [ ] Persist strategy and decision records transactionally with events.
- [ ] Verify main Agent delegates expert work rather than fabricating expert output.
- [ ] Run focused runtime tests and commit.

### Task 4: Expert dispatch, Critic and bounded improvement

**Files:**
- Create: `backend/app/orchestrator/critic.py`
- Modify: `backend/app/orchestrator/coo_runtime.py`
- Modify: `backend/app/orchestrator/agent_harness.py`
- Test: `backend/tests/test_coo_critic.py`

**Interfaces:**
- Produces: `score_expert_output`, `critic_review`, `improve_loop`.
- Consumes: existing AgentInvocation and Deliverable outputs.

- [ ] Write failing tests for five-dimension scores, evidence requirements and maximum two retries.
- [ ] Persist `AgentQualityScore` for every reviewed expert output.
- [ ] Route passing work forward, improvable work back to the same expert, and exhausted work to human review.
- [ ] Record prompt/model/tool versions and retry reason.
- [ ] Run focused tests and commit.

### Task 5: Approval, observation, reflection and experience verification

**Files:**
- Modify: `backend/app/orchestrator/coo_runtime.py`
- Modify: `backend/app/api/brain.py`
- Create: `backend/app/api/experience_memories.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_coo_closed_loop.py`

**Interfaces:**
- Produces: `resume_observation`, `performance_analysis`, `reflection`, `verify_experience_candidate`, `next_strategy`.
- Consumes: existing approval ledger, publish jobs, metric/account snapshots and verified imports.

- [ ] Write failing checkpoint/resume and verification-source tests.
- [ ] Pause on existing approval records and resume from persisted checkpoints.
- [ ] Compare real goal metrics with observed metrics; persist reflection without inventing missing values.
- [ ] Keep model summaries as candidates until rule or human verification.
- [ ] Add experience list/verify APIs and audit events.
- [ ] Run focused closed-loop tests and commit.

### Task 6: Operation Intelligence Score

**Files:**
- Modify: `backend/app/services/ai_coo.py`
- Modify: `backend/app/api/brain.py`
- Test: `backend/tests/test_operation_intelligence.py`

**Interfaces:**
- Produces: `calculate_operation_intelligence(task_id)` and `GET /brain/tasks/{id}/operation-intelligence`.
- Consumes: persisted strategy, evidence, quality, execution result and verified learning ledgers.

- [ ] Write failing deterministic-score tests.
- [ ] Implement weights: strategy 30%, evidence 25%, execution 25%, learning 20%.
- [ ] Return component coverage and “insufficient data” flags; never accept a model self-score.
- [ ] Run focused tests and commit.

### Task 7: Conversation UI and administrator execution detail

**Files:**
- Modify: `frontend/src/pages/BrainPage.tsx`
- Modify: `frontend/src/components/brain/*`
- Modify: `frontend/src/api/brain.ts`
- Modify: `frontend/src/types/brain.ts`
- Test: `frontend/src/pages/__tests__/BrainPage.test.tsx`
- Test: `frontend/src/components/brain/__tests__/*`

**Interfaces:**
- Produces: source-aware situation card, strategy summary, expert handoff, Critic result, compact approval, reflection and next-step messages.
- Consumes: additive AI COO endpoints and existing runtime stream.

- [ ] Write failing rendering tests for each business event and legacy-task fallback.
- [ ] Render readable business summaries; keep JSON and technical details hidden by default.
- [ ] Add admin execution detail for node, invocation, prompt version, tokens, cost, retries and safe errors.
- [ ] Keep the input fixed at the bottom and preserve stop/retry/reconnect behavior.
- [ ] Run frontend tests, lint and build; commit.

### Task 8: Production verification and rollback baseline

**Files:**
- Modify: `docs/tasks/current.md`
- Modify: `docs/superpowers/specs/2026-07-27-ai-coo-douyin-publishing-loop-design.md` only for verified implementation status

**Interfaces:**
- Produces: one deployable, reversible AI COO release candidate.
- Consumes: all previous tasks.

- [ ] Run all backend tests, Ruff and migration upgrade/downgrade checks.
- [ ] Run all frontend tests, lint and production build.
- [ ] Exercise a real local account scenario from user goal through strategy, expert, Critic and approval.
- [ ] Exercise missing-data behavior and legacy task rendering.
- [ ] Record exact verified status, remaining external-platform blockers and rollback instructions.
- [ ] Create a final version commit and local rollback tag.
