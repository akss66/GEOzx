# Main Agent Runtime Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让主 Agent 的结构化参数、附件、Tool、审批、质量状态和永久删除语义与产品承诺一致。

**Architecture:** 在 Conversation API 与 Router 之间建立不可变 `CapabilityRequest`，由 Skill Pydantic 输入模型验证结构化参数；Skill Runtime 按 Tool 元数据和审批策略执行状态机。附件和删除使用独立服务，并沿 org/user/account/thread/turn/run 全链路校验作用域。

**Tech Stack:** FastAPI、Pydantic v2、SQLAlchemy 2 async、Alembic、PostgreSQL、Redis worker、pytest、React 18、TypeScript、TanStack Query、Vitest。

## Global Constraints

- 不允许主 Agent 在专家或 Tool 未执行时生成“已完成”的正式成果。
- 外部副作用必须有人工审批和幂等键。
- 相同 `client_message_id` 不得产生重复消息、成果或 ToolCall。
- 不同用户和账号的会话、附件、成果及执行记录必须隔离。
- 所有行为变更遵循 RED → GREEN → REFACTOR，并在每个任务后提交。

---

### Task 1: CapabilityRequest 与确定性参数解析

**Files:**
- Create: `backend/app/schemas/capability_request.py`
- Create: `backend/app/services/capability_request.py`
- Modify: `backend/app/schemas/conversation.py`
- Modify: `backend/app/services/turn_execution.py`
- Test: `backend/tests/test_capability_request.py`
- Test: `backend/tests/test_turn_execution.py`

**Interfaces:**
- Produces: `CapabilityRequest`, `build_capability_request(turn, run, thread, user, request_payload)`, `extract_structured_constraints(message)`。
- Consumes: `CreateConversationTurnRequest`、`ConversationThread`、`ConversationTurn`、`AgentRun.request_payload`。

- [ ] **Step 1: 写失败测试，覆盖 14 天/10 个选题、30 秒脚本、只诊断不生成策略**

```python
def test_extracts_typed_operating_constraints():
    assert extract_structured_constraints("规划未来14天的10个选题") == {
        "days": 14,
        "topic_count": 10,
    }
    assert extract_structured_constraints("生成一个30秒脚本") == {
        "duration_seconds": 30,
    }
    assert extract_structured_constraints("只诊断，不生成策略") == {
        "generate_strategy": False,
        "requested_output": "diagnosis",
    }
```

- [ ] **Step 2: 运行测试并确认因为模块不存在而失败**

Run: `cd backend && uv run --project . python -m pytest tests/test_capability_request.py -q`

Expected: FAIL，提示 `app.services.capability_request` 不存在。

- [ ] **Step 3: 实现严格请求模型与无副作用参数解析器**

```python
JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

class CapabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    org_id: int = Field(gt=0)
    user_id: int = Field(gt=0)
    account_id: int = Field(gt=0)
    thread_id: int = Field(gt=0)
    turn_id: int = Field(gt=0)
    run_id: int = Field(gt=0)
    message: str = Field(min_length=1)
    requested_skill_code: str | None = None
    execution_preference: Literal["AUTO", "DISCUSS_ONLY", "FORMAL_TASK"]
    structured_input: dict[str, JsonValue] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    attachment_ids: list[int] = Field(default_factory=list)
```

解析器只提取无歧义数字和否定约束；无法确定的字段不猜测，交给 Skill 澄清。

- [ ] **Step 4: 将 CapabilityRequest 保存到 Turn 的安全路由快照并传给执行函数**

`_execute_composite_skill` 的签名增加 `capability_request: CapabilityRequest`，不再从多个松散对象重复推断用户要求。

- [ ] **Step 5: 运行单元测试和 Turn 回归测试**

Run: `cd backend && uv run --project . python -m pytest tests/test_capability_request.py tests/test_turn_execution.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/schemas/capability_request.py backend/app/services/capability_request.py backend/app/schemas/conversation.py backend/app/services/turn_execution.py backend/tests/test_capability_request.py backend/tests/test_turn_execution.py
git commit -m "feat: add typed main agent capability requests"
```

### Task 2: Skill 输入合并与输入指纹

**Files:**
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/models/skill_runtime.py`
- Modify: `backend/app/schemas/skills.py`
- Test: `backend/tests/test_operating_skills.py`
- Test: `backend/tests/test_skill_quality_recovery.py`

**Interfaces:**
- Consumes: `CapabilityRequest.structured_input`、目标 `SkillDefinition.input_model`。
- Produces: `build_skill_input(definition, capability_request) -> BaseModel` 和基于完整冻结输入的 `input_fingerprint`。

- [ ] **Step 1: 写失败测试，证明当前默认值覆盖用户约束**

```python
async def test_topic_skill_honors_user_days_and_topic_count(...):
    result = await skill_runtime.execute(
        session,
        capability_request=request_with(days=14, topic_count=10),
        skill_code="topic_planning",
        ...,
    )
    assert result.input_snapshot["days"] == 14
    assert result.input_snapshot["topic_count"] == 10
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend && uv run --project . python -m pytest tests/test_operating_skills.py -q`

Expected: FAIL，快照仍是 `days=30` 或 Skill 默认值。

- [ ] **Step 3: 用目标 Pydantic 模型验证结构化输入**

删除 `_execute_composite_skill` 中写死的 `days=30`。`SkillRuntime.execute` 接受 `capability_request`，将目标模型允许字段从 `structured_input` 中提取并 `model_validate`；多余字段禁止进入专家或 Tool。

- [ ] **Step 4: 将完整冻结输入写入 SkillRun 并参与恢复冲突检测**

同一 Run 使用不同输入恢复时返回 `SKILL_RECOVERY_INPUT_CONFLICT`；完全相同输入复用原 SkillRun。

- [ ] **Step 5: 验证选题、脚本、发布准备和复盘输入**

Run: `cd backend && uv run --project . python -m pytest tests/test_operating_skills.py tests/test_skill_quality_recovery.py tests/test_turn_execution.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/orchestrator/skill_runtime.py backend/app/models/skill_runtime.py backend/app/schemas/skills.py backend/tests/test_operating_skills.py backend/tests/test_skill_quality_recovery.py backend/tests/test_turn_execution.py
git commit -m "fix: honor structured skill inputs"
```

### Task 3: Tool 元数据与真实执行计划

**Files:**
- Create: `backend/app/orchestrator/skill_tool_plan.py`
- Modify: `backend/app/orchestrator/capability_registry.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/orchestrator/runtime_tools.py`
- Test: `backend/tests/test_skill_tool_plan.py`
- Test: `backend/tests/test_operating_skills.py`

**Interfaces:**
- Produces: `ToolEffect = read | prepare | side_effect`、`SkillToolStep`、`build_skill_tool_plan(definition)`。
- Consumes: `SkillDefinition.tool_codes` 和 Tool Registry 元数据。

- [ ] **Step 1: 写失败测试，要求发布准备产生真实 ToolCall**

```python
async def test_publishing_preparation_executes_declared_prepare_tool(...):
    result = await execute_skill("publishing_preparation")
    calls = await tool_calls_for(result.skill_run_id)
    assert [call.tool_code for call in calls] == ["publish_package_prepare"]
```

- [ ] **Step 2: 运行并确认失败，因为 Runtime 跳过非数据 Tool**

Run: `cd backend && uv run --project . python -m pytest tests/test_skill_tool_plan.py tests/test_operating_skills.py -q`

- [ ] **Step 3: 给 Tool Registry 增加 effect、approval_required、adapter_required 元数据**

`account.profile`、`account.data_context` 为 `read`；`publish_package_prepare` 为 `prepare`；真正平台发布 Tool 为 `side_effect`。

- [ ] **Step 4: 替换 Skill Runtime 的硬编码 Tool 白名单**

按 `read → experts → prepare → critic → approval → side_effect` 生成执行计划；未注册 Tool 返回 `SKILL_TOOL_UNREGISTERED`，Adapter 不可用返回 `CAPABILITY_ADAPTER_UNAVAILABLE`。

- [ ] **Step 5: 验证所有声明 Tool 都执行或形成明确阻塞记录**

Run: `cd backend && uv run --project . python -m pytest tests/test_skill_tool_plan.py tests/test_operating_skills.py tests/test_runtime_tool_executor.py tests/test_runtime_tools.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/orchestrator/skill_tool_plan.py backend/app/orchestrator/capability_registry.py backend/app/orchestrator/skill_runtime.py backend/app/orchestrator/runtime_tools.py backend/tests/test_skill_tool_plan.py backend/tests/test_operating_skills.py
git commit -m "fix: execute declared skill tools"
```

### Task 4: 审批与质量状态机

**Files:**
- Modify: `backend/app/models/enums.py`
- Modify: `backend/app/models/skill_runtime.py`
- Modify: `backend/app/orchestrator/skill_runtime.py`
- Modify: `backend/app/api/approvals.py`
- Modify: `backend/app/services/artifacts.py`
- Test: `backend/tests/test_operating_skills.py`
- Test: `backend/tests/test_approval_workspace_api.py`
- Test: `backend/tests/test_artifacts_api.py`

**Interfaces:**
- Produces: Skill 状态 `waiting_user`、`needs_review`、`blocked`，以及 `SkillQualityResult`。
- Consumes: `SkillDefinition.approval_policy`、`critic_policy` 和 Tool effect。

- [ ] **Step 1: 写失败测试，审批前不得完成、低质量不得显示普通完成**

```python
assert publishing_run.status == SkillRunStatus.WAITING_USER
assert publishing_run.approval_required is True
assert low_quality_run.status == SkillRunStatus.NEEDS_REVIEW
assert low_quality_artifact.status == "ready_for_review"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `cd backend && uv run --project . python -m pytest tests/test_operating_skills.py tests/test_approval_workspace_api.py -q`

- [ ] **Step 3: 实现 approval_policy 状态转换**

`before_tools` 在首个需要审批的 Tool 前暂停；`before_finish` 在草稿成果后暂停。审批 API 必须校验 user/org/account/thread/run/tool scope，批准和拒绝均写独立审计事件。

- [ ] **Step 4: 将 Critic 结果标准化**

为五个正式 Skill 统一保存 score、passed、issues、retryable、evidence_coverage；最多重做一次，第二次失败进入 `needs_review`。

- [ ] **Step 5: 运行审批、Skill 和成果测试**

Run: `cd backend && uv run --project . python -m pytest tests/test_operating_skills.py tests/test_approval_workspace_api.py tests/test_artifacts_api.py tests/test_skill_quality_recovery.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/models/enums.py backend/app/models/skill_runtime.py backend/app/orchestrator/skill_runtime.py backend/app/api/approvals.py backend/app/services/artifacts.py backend/tests/test_operating_skills.py backend/tests/test_approval_workspace_api.py backend/tests/test_artifacts_api.py
git commit -m "feat: enforce skill approval and quality gates"
```

### Task 5: 账号隔离附件资源

**Files:**
- Create: `backend/app/models/attachment.py`
- Create: `backend/app/schemas/attachment.py`
- Create: `backend/app/services/attachments.py`
- Create: `backend/app/api/attachments.py`
- Create: `backend/migrations/versions/20260803_0100_conversation_attachments.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/conversations.py`
- Modify: `backend/app/services/capability_request.py`
- Test: `backend/tests/test_attachments_api.py`
- Test: `backend/tests/test_conversation_api.py`

**Interfaces:**
- Produces: `ConversationAttachment`、`AttachmentContext`、上传/查询/删除 API。
- Consumes: 当前 user、account、thread scope 和对象存储 Adapter。

- [ ] **Step 1: 写失败 API 测试，覆盖所有者、账号和会话隔离**

其他用户、其他账号或其他会话引用附件均返回 404；扫描或解析未完成的附件不能进入 CapabilityRequest。

- [ ] **Step 2: 运行并确认路由不存在**

Run: `cd backend && uv run --project . python -m pytest tests/test_attachments_api.py -q`

- [ ] **Step 3: 新增附件模型与迁移**

字段包含 `org_id`、`created_by_id`、`account_id`、`thread_id`、`filename`、`mime_type`、`size_bytes`、`storage_key`、`sha256`、`scan_status`、`parse_status`、`parsed_context`；建立 scope、hash 和 thread 索引。

- [ ] **Step 4: 实现上传和作用域解析服务**

限制允许 MIME、单文件大小、每轮数量；文件名只用于展示，存储键使用随机 ID；日志不得包含原始内容。

- [ ] **Step 5: 提交 Turn 时解析为 AttachmentContext**

`attachment_ids` 去重并保持用户顺序；任何一个附件越权时整轮 fail closed，不允许部分忽略。

- [ ] **Step 6: 运行迁移与 API 测试**

Run: `cd backend && uv run --project . python -m pytest tests/test_attachments_api.py tests/test_conversation_api.py tests/test_migrations.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/app/models/attachment.py backend/app/schemas/attachment.py backend/app/services/attachments.py backend/app/api/attachments.py backend/migrations/versions/20260803_0100_conversation_attachments.py backend/app/main.py backend/app/api/conversations.py backend/app/services/capability_request.py backend/tests/test_attachments_api.py backend/tests/test_conversation_api.py backend/tests/test_migrations.py
git commit -m "feat: add account-scoped conversation attachments"
```

### Task 6: 前端附件交互

**Files:**
- Create: `frontend/src/api/attachments.ts`
- Create: `frontend/src/components/brain/AttachmentTray.tsx`
- Create: `frontend/src/components/brain/AttachmentTray.test.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.test.tsx`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `frontend/src/types.ts`

**Interfaces:**
- Produces: `uploadConversationAttachment`、`deleteConversationAttachment`、`AttachmentTray`。
- Consumes: 当前 account/thread 和 `sendConversationTurn.attachment_ids`。

- [ ] **Step 1: 写失败组件测试**

测试多文件选择、上传中禁用提交、失败重试、移除、提交真实 attachment IDs，且权限审批模式隐藏附件入口。

- [ ] **Step 2: 运行并确认失败**

Run: `cd frontend && pnpm.cmd exec vitest run src/components/brain/AttachmentTray.test.tsx src/components/brain/BrainComposer.test.tsx src/pages/BrainHome.test.tsx`

- [ ] **Step 3: 实现附件 API、Tray 与 Composer 状态**

不再显示“尚未接入”假入口；上传失败显示文件级错误，不清空已成功附件。

- [ ] **Step 4: 将真实 ID 提交到 Turn API**

发送成功后清空当前草稿附件；失败时保留附件供重试。

- [ ] **Step 5: 运行测试与类型检查**

Run: `cd frontend && pnpm.cmd exec vitest run src/components/brain/AttachmentTray.test.tsx src/components/brain/BrainComposer.test.tsx src/pages/BrainHome.test.tsx && pnpm.cmd exec tsc --noEmit`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/api/attachments.ts frontend/src/components/brain/AttachmentTray.tsx frontend/src/components/brain/AttachmentTray.test.tsx frontend/src/components/brain/BrainComposer.tsx frontend/src/components/brain/BrainComposer.test.tsx frontend/src/pages/BrainHome.tsx frontend/src/pages/BrainHome.test.tsx frontend/src/types.ts
git commit -m "feat: attach files to main agent turns"
```

### Task 7: 永久删除技术执行日志

**Files:**
- Create: `backend/app/models/audit_record.py`
- Create: `backend/migrations/versions/20260803_0200_minimal_audit_records.py`
- Modify: `backend/app/services/conversations.py`
- Modify: `backend/app/api/conversations.py`
- Test: `backend/tests/test_conversation_service.py`
- Test: `backend/tests/test_conversation_api.py`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces: `ConversationDeletionSummary`，包含每类删除数量和保留审计类别。
- Consumes: 用户拥有的终态 ConversationThread。

- [ ] **Step 1: 写失败测试，覆盖全部 `brain.runtime.*` 技术日志**

删除后不得存在带原 thread/turn/run/skill_run 标识的消息、Prompt、LLMCall 或技术事件；正式审批/发布/费用记录只保留最小审计字段。

- [ ] **Step 2: 运行并确认当前事件被解除关联而未删除**

Run: `cd backend && uv run --project . python -m pytest tests/test_conversation_service.py tests/test_conversation_api.py -q`

- [ ] **Step 3: 分类 AuditRecord 与 TechnicalEvent**

将审批决定、真实发布回执和费用合计复制为不可逆最小 AuditRecord；其余会话作用域 Event 全部永久删除，不再通过 `UPDATE ... SET thread_id=NULL` 保留。

- [ ] **Step 4: 扩展删除响应和前端确认文案所需字段**

响应列出 `messages_deleted`、`events_deleted`、`llm_calls_deleted`、`attachments_deleted`、`draft_artifacts_deleted` 和 `retained_audit_categories`。

- [ ] **Step 5: 运行删除、权限和迁移测试**

Run: `cd backend && uv run --project . python -m pytest tests/test_conversation_service.py tests/test_conversation_api.py tests/test_migrations.py -q`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/models/audit_record.py backend/migrations/versions/20260803_0200_minimal_audit_records.py backend/app/services/conversations.py backend/app/api/conversations.py backend/tests/test_conversation_service.py backend/tests/test_conversation_api.py backend/tests/test_migrations.py
git commit -m "fix: permanently delete conversation technical data"
```

### Task 8: 阶段一集成验收与功能开关

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/tests/test_brain_api.py`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `docs/runbooks/main-agent-v3-rollout.md`

**Interfaces:**
- Produces: `MAIN_AGENT_TYPED_RUNTIME_ENABLED` 灰度开关和回滚步骤。

- [ ] **Step 1: 增加端到端失败测试**

覆盖“14天10选题”“30秒脚本”“发布准备等待审批”“附件越权”“永久删除”五个场景。

- [ ] **Step 2: 运行后端主 Agent 全集、前端主 Agent 全集和生产构建**

```bash
cd backend && uv run --project . python -m pytest -q
cd frontend && pnpm.cmd exec vitest run && pnpm.cmd build
```

Expected: 全部 PASS，构建成功。

- [ ] **Step 3: 更新发布、监控和回滚 Runbook**

写明开关默认关闭、内部账号灰度、Run/Turn 终态一致性查询、ToolCall/审批监控和一键关闭开关的步骤。

- [ ] **Step 4: 提交**

```bash
git add backend/app/core/config.py backend/tests/test_brain_api.py frontend/src/pages/BrainHome.test.tsx docs/runbooks/main-agent-v3-rollout.md
git commit -m "chore: gate typed main agent runtime rollout"
```
