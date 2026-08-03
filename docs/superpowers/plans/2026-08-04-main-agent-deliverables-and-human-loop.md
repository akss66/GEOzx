# Main Agent Deliverables and Human Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让主 Agent 自动连续完成低风险运营工作，以强类型“方案与内容”交付可直接执行的运营产物，并在补充、改向、人工审批和故障后从正确位置继续。

**Architecture:** 复用现有 AgentRun 租约、LangGraph PostgreSQL checkpoint、SkillRun 和 Deliverable 版本机制，新增消息 steering 关系、业务动作契约和持久 interrupt。写操作按账号串行，只重做受新要求影响的阶段；用户侧不暴露 accepted、dead_letter 或技术错误。

**Tech Stack:** FastAPI、Pydantic、SQLAlchemy Async、PostgreSQL、LangGraph checkpoint、ARQ、React、TanStack Query、pytest、Vitest。

## Global Constraints

- 分析、选题、拍摄稿、质量检查和发布顺序默认自动连续完成。
- 仅在缺少关键输入、业务方向冲突、真实人员/排期变更、高风险动作和对外发布前暂停。
- 现阶段抖音发布必须人工最终确认并手动执行。
- 用户侧不得出现“采用成果”“dead_letter”“HTTP 409”“Harness”等内部术语。
- 每个运营产物必须绑定账号、会话、Turn、数据周期、证据、版本、质量和具体下一步。
- 用户补充要求只使受影响的阶段失效，不重新执行已经安全复用的步骤。
- 同一账号的副作用写操作串行，不同账号完全隔离。

---

### Task 1: 将 Artifact 输出扩展为强类型业务交付契约

**Files:**
- Modify: `backend/app/schemas/artifacts.py`
- Modify: `backend/app/services/artifacts.py`
- Modify: `backend/tests/test_artifacts_api.py`
- Modify: `frontend/src/types.ts`

**Interfaces:**
- Produces: `ArtifactOut.presentation`、`ArtifactOut.next_actions`、`DeliverableActionOut`。

- [ ] **Step 1: 写具体交付文案与动作失败测试**

```py
assert artifact.presentation.type_label == "口播拍摄稿"
assert artifact.presentation.completion_label == "已生成 5 条可直接拍摄的口播稿"
assert artifact.next_actions[0].code == "create_shoot_task"
assert artifact.next_actions[0].label == "创建拍摄任务"
```

- [ ] **Step 2: 运行 Artifact API 测试确认字段不存在**

Run: `cd backend && pytest tests/test_artifacts_api.py -q`
Expected: FAIL.

- [ ] **Step 3: 定义后端业务动作类型**

```py
class DeliverableActionOut(BaseModel):
    code: Literal[
        "create_optimization_plan", "generate_production_briefs", "create_shoot_task",
        "add_to_schedule", "prepare_manual_publish", "record_publish_result",
        "generate_next_iteration", "request_revision", "export",
    ]
    label: str
    requires_confirmation: bool = False
```

- [ ] **Step 4: 由 artifact_type 和结构化数量生成 presentation**

未知类型降级为“运营报告”和“查看完整报告”，不得降级为“成果”或“采用”。内部 `status="accepted"` 暂时保留兼容，但序列化为用户标签“已确认”。

- [ ] **Step 5: 运行 API、Schema 和前端类型检查**

Run: `cd backend && pytest tests/test_artifacts_api.py -q && cd ../frontend && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/app/schemas/artifacts.py backend/app/services/artifacts.py backend/tests/test_artifacts_api.py frontend/src/types.ts
git commit -m "feat: expose typed operations deliverables and actions"
```

### Task 2: 用业务动作端点替代前端“采用”工作流

**Files:**
- Create: `backend/app/api/deliverable_actions.py`
- Create: `backend/app/services/deliverable_actions.py`
- Create: `backend/tests/test_deliverable_actions_api.py`
- Modify: `backend/app/main.py`
- Modify: `frontend/src/api/brain.ts`
- Modify: `frontend/src/pages/BrainHome.tsx`

**Interfaces:**
- Produces: `POST /artifacts/{artifact_id}/actions/{action_code}`。

- [ ] **Step 1: 写动作权限、幂等和账号隔离失败测试**

相同 `Idempotency-Key` 两次创建拍摄任务只返回同一任务；其他账号访问返回 404；不支持的动作返回结构化 `action_unavailable`。

- [ ] **Step 2: 运行测试确认动作端点不存在**

Run: `cd backend && pytest tests/test_deliverable_actions_api.py -q`
Expected: FAIL with 404.

- [ ] **Step 3: 实现动作注册表**

```py
ACTION_HANDLERS: dict[str, DeliverableActionHandler] = {
    "create_shoot_task": create_shoot_task,
    "add_to_schedule": add_to_schedule,
    "generate_next_iteration": generate_next_iteration,
    "request_revision": request_revision,
}
```

执行前验证 Artifact 是当前账号最新可用版本、动作在 `next_actions` 中、所需人工确认已满足。

- [ ] **Step 4: 前端删除 acceptArtifact 调用**

`BrainHome` 只调用 `executeDeliverableAction(artifactId, actionCode, idempotencyKey)`；保留旧 acceptance API 供历史客户端兼容一个发布周期，但新 UI 不调用。

- [ ] **Step 5: 运行前后端动作测试**

Run: `cd backend && pytest tests/test_deliverable_actions_api.py tests/test_artifacts_api.py -q && cd ../frontend && npm test -- BrainHome.test.tsx brain.test.ts`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/app/api/deliverable_actions.py backend/app/services/deliverable_actions.py backend/tests/test_deliverable_actions_api.py backend/app/main.py frontend/src/api/brain.ts frontend/src/pages/BrainHome.tsx
git commit -m "feat: execute explicit operations actions from deliverables"
```

### Task 3: 建立补充、修改、停止和独立查询的 Steering 契约

**Files:**
- Modify: `backend/app/models/conversation.py`
- Create: `backend/migrations/versions/20260804_0200_turn_steering.py`
- Modify: `backend/app/schemas/conversation.py`
- Create: `backend/app/services/turn_steering.py`
- Create: `backend/tests/test_turn_steering.py`
- Modify: `backend/app/api/conversations.py`

**Interfaces:**
- Produces: `TurnSteeringMode`、`classify_turn_steering(...)`、`target_turn_id`。

- [ ] **Step 1: 写确定性 Steering 失败测试**

```py
assert classify_turn_steering("第一条不要讲价格", active).mode == "supplement"
assert classify_turn_steering("先停一下", active).mode == "stop"
assert classify_turn_steering("重新按获客目标规划", active).mode == "replace_goal"
assert classify_turn_steering("顺便看看昨天的数据", active).mode == "independent_query"
```

- [ ] **Step 2: 运行测试确认所有新消息都创建独立执行**

Run: `cd backend && pytest tests/test_turn_steering.py tests/test_conversation_api.py -q`
Expected: FAIL.

- [ ] **Step 3: 增加关联字段和公开结果**

ConversationTurn 增加 nullable `target_turn_id` 和 `steering_mode`；`TurnSubmissionOut` 返回系统判断和用户可读说明，例如“已加入当前 5 条拍摄稿的要求”。

- [ ] **Step 4: 采用规则优先、模型兜底的判断顺序**

显式停止、继续、重做和明显补充先确定性识别；只有歧义消息调用轻量模型。低置信度时创建独立查询，不静默取消当前任务。

- [ ] **Step 5: 运行 Steering 与 API 测试**

Run: `cd backend && pytest tests/test_turn_steering.py tests/test_conversation_api.py -q`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/app/models/conversation.py backend/migrations/versions/20260804_0200_turn_steering.py backend/app/schemas/conversation.py backend/app/services/turn_steering.py backend/tests/test_turn_steering.py backend/app/api/conversations.py
git commit -m "feat: steer active main agent work from new messages"
```

### Task 4: 只使受影响步骤失效并复用安全检查点

**Files:**
- Create: `backend/app/orchestrator/step_dependencies.py`
- Create: `backend/tests/test_step_dependencies.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/services/turn_execution.py`
- Modify: `backend/tests/test_turn_execution.py`

**Interfaces:**
- Produces: `affected_steps(skill_code, changed_constraints) -> set[str]`。

- [ ] **Step 1: 写局部失效失败测试**

“不要讲价格”只失效 `production_briefs → quality_review → publishing_schedule`，不得重新执行 `read_account_data` 和 `benchmark_analysis`。

- [ ] **Step 2: 运行测试确认当前只能整轮重做**

Run: `cd backend && pytest tests/test_step_dependencies.py tests/test_turn_execution.py -q`
Expected: FAIL.

- [ ] **Step 3: 为 Skill 注册显式依赖图**

```py
STEP_DEPENDENCIES = {
    "production_briefs": {"topics", "brand_constraints", "product_facts"},
    "quality_review": {"production_briefs"},
    "publishing_schedule": {"topics", "production_briefs"},
}
```

- [ ] **Step 4: 从 LangGraph checkpoint 恢复最早受影响节点**

复制旧 Run 的已确认输入与可复用阶段输出，创建关联的新 attempt；原 Run 标记 `stopped` 而非失败；所有复用和失效阶段写入 TurnEvent。

- [ ] **Step 5: 运行局部恢复和专家调用计数测试**

Run: `cd backend && pytest tests/test_step_dependencies.py tests/test_turn_execution.py tests/test_worker.py -q`
Expected: PASS，数据 Tool 和无关专家调用次数不增加。

- [ ] **Step 6: 提交**

```bash
git add backend/app/orchestrator/step_dependencies.py backend/tests/test_step_dependencies.py backend/app/orchestrator/skill_runtime.py backend/app/services/turn_execution.py backend/tests/test_turn_execution.py
git commit -m "feat: resume main agent work from affected steps"
```

### Task 5: 将人工暂停建模为持久 Interrupt

**Files:**
- Create: `backend/app/models/turn_interrupt.py`
- Create: `backend/migrations/versions/20260804_0300_turn_interrupts.py`
- Create: `backend/app/schemas/turn_interrupt.py`
- Create: `backend/app/services/turn_interrupts.py`
- Create: `backend/app/api/turn_interrupts.py`
- Create: `backend/tests/test_turn_interrupts_api.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Produces: `TurnInterrupt`、`POST /turn-interrupts/{id}/resolve`。

- [ ] **Step 1: 写暂停、拒绝和恢复失败测试**

发布动作产生 `waiting_user` Interrupt；批准后同一 AgentRun 从 checkpoint 恢复；拒绝后跳过动作并生成明确结果；重复 resolve 返回同一决定。

- [ ] **Step 2: 运行测试确认现有 Approval 不能表达所有暂停**

Run: `cd backend && pytest tests/test_turn_interrupts_api.py -q`
Expected: FAIL.

- [ ] **Step 3: 定义 Interrupt 模型**

```py
class TurnInterrupt(Base, TimestampMixin):
    id: int
    org_id: int
    account_id: int
    thread_id: int
    turn_id: int
    run_id: int
    kind: str
    public_message: str
    response_schema: dict
    status: str
    resolved_by_id: int | None
```

- [ ] **Step 4: 连接 Skill approval_policy 与人工发布**

低风险内部 Tool 不暂停；人员任务、排期修改和人工发布包根据 Tool metadata 创建 Interrupt。解决后写 durable TurnEvent 并重新入队原 AgentRun。

- [ ] **Step 5: 运行 Interrupt、Skill 和发布测试**

Run: `cd backend && pytest tests/test_turn_interrupts_api.py tests/test_content_publishing_skill.py tests/test_skill_tool_plan.py -q`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/app/models/turn_interrupt.py backend/migrations/versions/20260804_0300_turn_interrupts.py backend/app/schemas/turn_interrupt.py backend/app/services/turn_interrupts.py backend/app/api/turn_interrupts.py backend/tests/test_turn_interrupts_api.py backend/app/main.py
git commit -m "feat: persist and resume human main agent interrupts"
```

### Task 6: 按账号串行副作用并强化自动恢复

**Files:**
- Create: `backend/app/services/account_execution_lane.py`
- Create: `backend/tests/test_account_execution_lane.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/services/turn_observability.py`

**Interfaces:**
- Produces: `account_execution_lane(account_id, operation_kind)` 上下文管理器。

- [ ] **Step 1: 写同账号写串行、跨账号并行失败测试**

两个账号 A 的排期写不得重叠；账号 A 与 B 可以同时执行；租约过期后新 Worker 接管但不会重复副作用。

- [ ] **Step 2: 运行测试确认只有 Run 级租约**

Run: `cd backend && pytest tests/test_account_execution_lane.py tests/test_worker.py -q`
Expected: FAIL.

- [ ] **Step 3: 使用 PostgreSQL advisory lock 建立账号执行通道**

只对 `idempotent_write` 和 `non_idempotent_write` 获取 `pg_advisory_xact_lock(account_id)`；只读 Tool 不加账号写锁。

- [ ] **Step 4: 收敛错误分类和重试次数**

业务冲突、权限、输入和能力不可用设置 `retryable=False`；网络、限流和瞬时依赖最多两次指数退避；终态统一关闭 Turn、AgentRun 和 SkillRun。

- [ ] **Step 5: 运行 Worker、Skill 和终态一致性测试**

Run: `cd backend && pytest tests/test_account_execution_lane.py tests/test_worker.py tests/test_skill_quality_recovery.py tests/test_turn_execution.py -q`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/account_execution_lane.py backend/tests/test_account_execution_lane.py backend/app/worker.py backend/app/orchestrator/skill_runtime.py backend/app/services/turn_observability.py
git commit -m "fix: serialize account side effects and recover safely"
```

### Task 7: 建立“待处理”与具体通知

**Files:**
- Create: `backend/app/schemas/pending_work.py`
- Create: `backend/app/services/pending_work.py`
- Create: `backend/app/api/pending_work.py`
- Create: `backend/tests/test_pending_work_api.py`
- Create: `frontend/src/components/brain/PendingWork.tsx`
- Create: `frontend/src/components/brain/PendingWork.test.tsx`
- Modify: `frontend/src/pages/BrainHome.tsx`

**Interfaces:**
- Produces: `GET /accounts/{account_id}/pending-work`。

- [ ] **Step 1: 写动作分组和账号隔离失败测试**

待处理只包含“待补充资料、待确认方向、待拍摄、待手动发布、待补录数据”；其他账号条目不可见；完成动作后条目消失。

- [ ] **Step 2: 运行 API 与组件测试确认不存在**

Run: `cd backend && pytest tests/test_pending_work_api.py -q && cd ../frontend && npm test -- PendingWork.test.tsx`
Expected: FAIL.

- [ ] **Step 3: 从 Interrupt、拍摄任务、排期和数据新鲜度投影待处理**

每条返回 `action_label`、`account_id`、`turn_id`、`due_at`、`reason` 和 `next_step_after_completion`，不返回内部状态码。

- [ ] **Step 4: 实现顶部“待处理”页签**

按具体动作分组；点击回到来源 WorkTurn 或相应业务页面；只有需要人工行动、完成、恢复失败和数据到期才触发通知。

- [ ] **Step 5: 运行前后端测试**

Run: `cd backend && pytest tests/test_pending_work_api.py -q && cd ../frontend && npm test -- PendingWork.test.tsx BrainHome.test.tsx`
Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/app/schemas/pending_work.py backend/app/services/pending_work.py backend/app/api/pending_work.py backend/tests/test_pending_work_api.py frontend/src/components/brain/PendingWork.tsx frontend/src/components/brain/PendingWork.test.tsx frontend/src/pages/BrainHome.tsx
git commit -m "feat: surface concrete operator pending work"
```

### Task 8: 完成端到端运营 Worker 验收与灰度

**Files:**
- Create: `backend/tests/test_main_agent_worker_contract.py`
- Create: `frontend/e2e/main-agent-worker-loop.spec.ts`
- Modify: `docs/runbooks/main-agent-v3-rollout.md`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Tasks 1-7 和前两份计划的交付。
- Produces: 生产发布门和一键回滚条件。

- [ ] **Step 1: 编写完整运营任务契约测试**

输入“结合最近数据和对标内容，规划并制作下周抖音内容”，断言产生 5 个选题、5 条口播拍摄稿、7 天发布安排以及具体下一步动作。

- [ ] **Step 2: 编写中途改向 E2E**

在第 4 条拍摄稿生成时输入“第一条不要讲价格”，断言只重做受影响内容、消息显示“已加入当前任务”、数据读取和对标分析不重复。

- [ ] **Step 3: 编写人工发布暂停 E2E**

断言系统生成手动发布清单后进入待处理，不调用自动发布 Tool；用户记录发布完成后创建后续数据补录事项。

- [ ] **Step 4: 更新 CI 和灰度门**

CI 运行后端 Worker 契约、前端主 Agent 测试、类型检查、构建和两条 E2E。生产先开放一个内部账号，24 小时内要求重复消息、跨账号污染、重复副作用和状态错位均为 0。

- [ ] **Step 5: 运行完整质量门**

Run: `cd backend && pytest tests/test_main_agent_worker_contract.py -q && cd ../frontend && npm test && npm run lint && npm run build && npm run test:e2e -- main-agent-worker-loop.spec.ts`
Expected: all PASS.

- [ ] **Step 6: 提交**

```bash
git add backend/tests/test_main_agent_worker_contract.py frontend/e2e/main-agent-worker-loop.spec.ts docs/runbooks/main-agent-v3-rollout.md .github/workflows/ci.yml
git commit -m "test: enforce the complete operations worker loop"
```
