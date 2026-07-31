# Task 5 Brief — RuntimeScope 与成果来源约束

## Ownership

你负责 Task 5 中以下文件及其必要的定向测试：

- `backend/app/models/conversation.py`
- `backend/app/models/agent_runtime.py`
- `backend/app/models/skill_runtime.py`
- `backend/app/models/content.py`
- `backend/app/models/brain.py`
- `backend/app/orchestrator/runtime_scope.py`
- `backend/app/orchestrator/agent_harness.py`
- `backend/app/orchestrator/skill_runtime.py`
- `backend/app/orchestrator/tool_executor.py`
- `backend/app/services/runtime_deliverables.py`
- `backend/app/services/artifacts.py`
- `backend/app/services/conversations.py`
- `backend/migrations/versions/20260730_0300_runtime_scope_constraints.py`
- 对应 backend tests。

你不是代码库中唯一的执行者。不要回退其他人的修改；如发现并行改动，应调整实现兼容它。

## Required workflow

1. 严格 TDD：先写失败测试并记录 RED，再实现最小闭环。
2. 所有 V3 运行态写入用一个不可变 `RuntimeScope`，不要继续散传 4–7 个裸 ID。
3. 保持 legacy 全空 provenance 兼容；不得为了通过约束伪造来源。
4. 完成后运行定向测试、Ruff、Alembic heads/迁移 SQL 编译和 `git diff --check`。
5. 提交代码；报告写入 `.superpowers/sdd/2026-07-30-operations-brain-v3/task-5-report.md` 并提交。

## RuntimeScope contract

至少包含：

```python
@dataclass(frozen=True)
class RuntimeScope:
    org_id: int
    user_id: int
    account_id: int
    thread_id: int
    turn_id: int
    run_id: int
    task_id: int
    skill_run_id: int | None = None
```

建议分阶段构造：

- `from_conversation(...)`：校验 user/org/thread/account/turn/run。
- `bind_task(...)`：校验 task org、run.task_id、任务账户上下文。
- `bind_skill(...)`：校验 SkillRun 与 task/run/thread/turn/org 完整一致。

V3 路径缺少任何 required ID 或只填半套 scope 时，应在 flush 前返回稳定的业务冲突；legacy writer 只允许 provenance 全空，不能半空。

## Database constraints

数据库只强制“已完整填充的复合来源必须一致”。SQL 复合 FK 为 `MATCH SIMPLE`，不能代替应用层 RuntimeScope。

优先增加并测试：

- `brain_tasks(id, org_id)`，以及 `agent_runs(task_id, org_id)`、`skill_runs(task_id, org_id)` 的复合 FK。
- `conversation_turns(id, thread_id)`，以及 Invocation/ToolCall/Deliverable 的 turn/thread 复合 FK。
- `agent_runs(id, task_id, thread_id, turn_id)`，以及 SkillRun/Invocation 的对应复合 FK。
- `agent_runs(id, thread_id, turn_id)`，以及 Deliverable 的 run/thread/turn 复合 FK。
- `skill_runs(id, task_id, run_id, thread_id, turn_id)`，以及 Invocation 的对应复合 FK。
- `skill_runs(id, task_id, thread_id, turn_id)`，以及 ToolCall 的对应复合 FK。
- `skill_runs(id, run_id, thread_id, turn_id)`，以及 Deliverable 的对应复合 FK。
- `agent_invocations(id, task_id)`，以及 ToolCall 的 invocation/task 复合 FK。

不要在复合 FK 上使用 `SET NULL` 去清空不可空的 task/org；保持现有单列 FK 删除动作和会话删除服务的显式清理顺序。

## Migration safety

迁移前执行只读 preflight，并在冲突时整体失败：

- Run task 与 org 不一致。
- SkillRun 的 task/run/thread/turn/org 不一致。
- Invocation、ToolCall、Deliverable 的多来源相互冲突。
- ContentItem.account_id 与 ConversationThread.account_id 不一致。
- 任意半 scope。

只允许以下确定性补齐：

- 已有 canonical `skill_run_id`，其来源完整且与已填字段无冲突。
- ToolCall 已有 canonical `invocation_id`，其来源完整且与已填字段无冲突。
- 无任何 V3 source 的 legacy 行保持全空。

不得按标题、时间、`BrainTask.thread_id`、JSON `account_ids` 或“最接近记录”推断。

## Runtime write boundaries

- Harness：接收/构造完整 scope；Invocation、ToolCall、Deliverable 只从 scope 赋来源。
- ToolExecutor：执行前校验 scope；幂等复用必须比较完整 scope，尤其是 `invocation_id`。
- Artifact writer：新增 `write_runtime_deliverable(...)` 作为 Harness 和 Skill Runtime 的唯一正式成果写入口；验证 ContentItem account 与 thread account。
- Artifact revision：复制 provenance 前校验历史来源；坏 lineage 返回 409。
- `agent_workspace.py` 和旧 Engine 保持显式 legacy 模式，不强迫伪造 RuntimeScope。

## Required tests

- 跨 user/org/account/thread/turn/task/run/skill_run 全部拒绝，且失败时不新增 Invocation、ToolCall、Deliverable。
- 数据库直接构造交叉来源对象时复合 FK 拒绝。
- legacy 全空来源仍可写；V3 半 scope 拒绝。
- 同 idempotency key 但不同 invocation/scope 返回 `ToolIdempotencyConflict`。
- Artifact writer 不能跨账号，revision 不能复制坏来源。
- migration 覆盖可确定补齐、冲突回滚、全空 legacy。
- 会话删除后正式 Artifact/Task 保留，运行来源置空。

## Out of scope

- 不在本 Task 修改 Skill 版本冻结/专家质量门（Task 6）。
- 不在本 Task 修改写 Tool exactly-once/outbox（Task 7）。
- 不在本 Task 修改前端（Task 8）。
