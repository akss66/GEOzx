# Main Agent Operation Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将账号定位、内容生产、排期、发布、互动、复盘和迭代补齐为可发现、可审计的运营生命周期 Skill。

**Architecture:** 每个业务 Skill 使用独立 Pydantic 输入输出契约、专家阶段、Tool 计划、质量门和成果类型；组合 Skill 只消费已确认成果并编排其他 Skill，不代替专家产出专业结论。Capability Registry 是前端能力菜单、主 Agent 能力说明和 Runtime 的唯一来源。

**Tech Stack:** FastAPI、Pydantic v2、Skill Runtime、Agent Harness、Tool Registry、SQLAlchemy、pytest、React、TypeScript、Vitest。

## Global Constraints

- 阶段一 Runtime 契约测试必须全部通过后才能开始本计划。
- 每个 Skill 独立上线，未完成 Skill 状态必须为 `coming_soon` 或 `needs_connection`。
- 用户可从任意环节进入，缺少上下文时只询问最少字段。
- 真实发布必须经过审批并返回平台回执；无 Adapter 时明确阻塞。

---

### Task 1: 统一 Capability Registry 与可用状态

**Files:**
- Modify: `backend/app/orchestrator/capability_registry.py`
- Modify: `backend/app/schemas/skills.py`
- Modify: `backend/app/api/brain.py`
- Modify: `frontend/src/api/brain.ts`
- Modify: `frontend/src/types.ts`
- Test: `backend/tests/test_capability_registry.py`
- Test: `backend/tests/test_skills_api.py`
- Test: `frontend/src/api/brain.test.ts`

**Interfaces:**
- Produces: `CapabilityAvailability = available | needs_input | needs_connection | coming_soon` 和 `required_context`。

- [ ] **Step 1:** 写失败测试，要求 API 按平台、账号连接和权限返回真实状态。
- [ ] **Step 2:** 运行 `cd backend && uv run --project . python -m pytest tests/test_capability_registry.py tests/test_skills_api.py -q`，确认失败。
- [ ] **Step 3:** 扩展 Registry，使能力说明、快捷入口和 Runtime 读取同一 SkillDefinition。
- [ ] **Step 4:** 前端类型和 API 消费 `availability`、`reason`、`required_context`，不自行猜测。
- [ ] **Step 5:** 运行后端 API 与前端 API 测试并确认通过。
- [ ] **Step 6:** 提交 `feat: expose truthful capability availability`。

### Task 2: 账号定位 Skill

**Files:**
- Create: `backend/app/orchestrator/skills/account_positioning.py`
- Modify: `backend/app/orchestrator/skill_registry.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Test: `backend/tests/test_account_positioning_skill.py`

**Interfaces:**
- Input: `business_goal`、`target_audience`、`differentiation_constraints`。
- Output: `positioning_statement`、`audience`、`content_pillars`、`tone`、`boundaries`、`evidence_refs`。

- [ ] **Step 1:** 写失败测试，要求 `01-positioning` 专家执行且正式成果包含证据和边界。
- [ ] **Step 2:** 运行测试确认 Skill 未注册。
- [ ] **Step 3:** 实现严格输入输出契约、数据 Tool、定位专家和 required critic。
- [ ] **Step 4:** 注册 Skill，并允许用户无历史定位时从本轮输入启动。
- [ ] **Step 5:** 运行 Skill、Registry 和 Artifact 测试。
- [ ] **Step 6:** 提交 `feat: add account positioning skill`。

### Task 3: 视觉 Brief 与内容排期 Skill

**Files:**
- Create: `backend/app/orchestrator/skills/visual_brief_generation.py`
- Create: `backend/app/orchestrator/skills/content_calendar_planning.py`
- Modify: `backend/app/orchestrator/skill_registry.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Test: `backend/tests/test_visual_brief_skill.py`
- Test: `backend/tests/test_content_calendar_skill.py`

**Interfaces:**
- Visual input consumes confirmed script/topic artifact IDs; output includes cover copy, composition, shot list, asset checklist and platform constraints.
- Calendar input consumes confirmed topic/script artifact IDs; output includes dates, items, owners, readiness and dependencies.

- [ ] **Step 1:** 写失败测试，确保其他账号成果 ID 被拒绝，未确认成果需要用户确认。
- [ ] **Step 2:** 运行测试确认失败。
- [ ] **Step 3:** 实现两个 Skill 的输入输出、专家阶段和成果引用校验。
- [ ] **Step 4:** 注册 Skill，添加 `required_context` 与平台支持矩阵。
- [ ] **Step 5:** 运行两个 Skill、权限和成果版本测试。
- [ ] **Step 6:** 提交 `feat: add visual brief and content calendar skills`。

### Task 4: 发布与平台回执 Skill

**Files:**
- Create: `backend/app/orchestrator/skills/content_publishing.py`
- Modify: `backend/app/orchestrator/runtime_tools.py`
- Modify: `backend/app/services/publishing.py`
- Modify: `backend/app/orchestrator/skill_registry.py`
- Test: `backend/tests/test_content_publishing_skill.py`
- Test: `backend/tests/test_publishing_service.py`

**Interfaces:**
- Input: approved publish package artifact ID、scheduled_at、visibility、comment settings。
- Output: platform receipt ID、published_at、status、retryability、source artifact version。

- [ ] **Step 1:** 写失败测试，覆盖未审批、Adapter 缺失、幂等重放和成功回执。
- [ ] **Step 2:** 运行测试确认 Skill 不存在。
- [ ] **Step 3:** 实现 `content_publishing`，只调用 `side_effect` Tool，审批包版本变化时旧审批失效。
- [ ] **Step 4:** 将 Adapter 缺失映射为 `needs_connection`/`blocked`，不生成伪回执。
- [ ] **Step 5:** 运行发布、审批、Tool outbox 和恢复测试。
- [ ] **Step 6:** 提交 `feat: add approval-gated content publishing`。

### Task 5: 互动复盘 Skill

**Files:**
- Create: `backend/app/orchestrator/skills/engagement_review.py`
- Modify: `backend/app/orchestrator/skill_registry.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Test: `backend/tests/test_engagement_review_skill.py`

**Interfaces:**
- Input: `days`、`content_item_ids`、`response_scope`。
- Output: common questions、sentiment、response guidelines、content opportunities、evidence refs。

- [ ] **Step 1:** 写失败测试，要求 `08-customer-service` 专家只使用当前账号互动数据。
- [ ] **Step 2:** 运行测试确认失败。
- [ ] **Step 3:** 实现 Skill、数据 Tool 和 required critic；不自动回复外部评论。
- [ ] **Step 4:** 注册能力状态；互动数据不足时返回 `needs_input` 而非编造结论。
- [ ] **Step 5:** 运行 Skill、证据和账号隔离测试。
- [ ] **Step 6:** 提交 `feat: add engagement review skill`。

### Task 6: 运营迭代组合 Skill

**Files:**
- Create: `backend/app/orchestrator/skills/operation_iteration.py`
- Create: `backend/app/orchestrator/composite_skill_runtime.py`
- Modify: `backend/app/orchestrator/skill_registry.py`
- Test: `backend/tests/test_operation_iteration_skill.py`

**Interfaces:**
- Input: confirmed review artifact ID、cycle_days、optional positioning artifact ID。
- Output: child Skill graph、dependencies、approval points 和最终 execution plan artifact。

- [ ] **Step 1:** 写失败测试，要求组合 Skill 只引用已确认成果，且不自行生成定位/脚本结论。
- [ ] **Step 2:** 运行测试确认失败。
- [ ] **Step 3:** 实现子 Skill DAG：复盘 → 选题 → 脚本/视觉 → 排期 → 发布准备。
- [ ] **Step 4:** 子 Skill 失败时组合运行保持可恢复，已完成子成果不重复执行。
- [ ] **Step 5:** 运行组合、幂等、恢复和成果投影测试。
- [ ] **Step 6:** 提交 `feat: orchestrate operation iteration cycles`。

### Task 7: 移除已迁移意图的旧通用任务路径

**Files:**
- Modify: `backend/app/services/turn_execution.py`
- Modify: `backend/app/orchestrator/capability_router.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Test: `backend/tests/test_turn_execution.py`
- Test: `backend/tests/test_capability_router.py`

**Interfaces:**
- Produces: typed Skill、clarify、unsupported 三种明确结果；运营意图不再创建统一 `REVIEW_OPTIMIZATION`。

- [ ] **Step 1:** 写失败测试，确保定位、选题、脚本、发布、互动、复盘意图不进入 `_execute_operation_task`。
- [ ] **Step 2:** 运行测试，记录当前错误路径。
- [ ] **Step 3:** 将已迁移意图映射到 typed Skill；无法映射的正式任务返回澄清或 unsupported。
- [ ] **Step 4:** 删除对应兼容分支和无用测试夹具，不重构无关旧运行图。
- [ ] **Step 5:** 运行 Router、Turn、Brain Runtime 回归测试。
- [ ] **Step 6:** 提交 `refactor: route operations through typed skills`。

### Task 8: 阶段二用户场景验收

**Files:**
- Create: `backend/tests/test_main_agent_operation_lifecycle.py`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `docs/runbooks/main-agent-v3-rollout.md`

**Interfaces:**
- Produces: 从任意环节进入的 10 场景验收矩阵。

- [ ] **Step 1:** 实现定位、选题、脚本、视觉、排期、发布准备、发布阻塞、互动、复盘、下一周期十个集成场景。
- [ ] **Step 2:** 验证每个场景的专家、Tool、质量门、审批、成果和错误状态。
- [ ] **Step 3:** 运行后端全量测试、前端全量测试和生产构建。
- [ ] **Step 4:** 更新灰度开关、平台连接前置条件和回滚步骤。
- [ ] **Step 5:** 提交 `test: cover main agent operation lifecycle`。
