# Task 7 Brief — 对话永久删除与外部副作用幂等

## Ownership

负责计划 Task 7 列出的会话删除、运行时状态守卫、Tool side-effect outbox、adapter 幂等和 0500 迁移，以及定向测试。你不是代码库唯一执行者；不得回退其他任务的变更，必须复用 Task 4 的统一状态机、Task 5 的 RuntimeScope / provenance、Task 6 的 SkillRuntime 边界。

## Workflow

1. 严格 TDD，先记录 RED。
2. 先做删除安全边界，再做 Tool side-effect/outbox，最后收口 worker 与 runtime 状态映射。
3. 非幂等写入禁止自动重放；任何不确定结果必须进入 `ambiguous` / 等待人工决策路径。
4. 完成后运行定向测试、Ruff、Alembic heads/迁移编译、`git diff --check`，提交代码并写 Task 7 报告。

## Conversation deletion boundary

- 只有会话 owner 可以永久删除自己的对话；管理员也不能越权删除他人会话。
- 删除前必须锁定并检查 Thread / Turn / AgentRun / SkillRun / Invocation / ToolCall。
- 任一 active / paused / unknown 状态存在时返回稳定 409；仅 empty 或完整 terminal 会话可删。
- 删除仅清除 owned conversation data：用户消息、AI 回复、技术事件、owned LLMCall、read-only ToolCall/Attempt。
- 正式成果、BrainTask、Content、Deliverable、formal Event 需要保留；删除后清空可空 provenance，不得误删 shared task 数据。
- 删除与 worker acquire 并发时必须 fail closed，不能出现“删了一半又继续运行”。

## Side-effect model

`ToolSpec.side_effect_level` 必填：

- `read`
- `idempotent_write`
- `non_idempotent_write`

并新增持久化 attempt / outbox 状态机：

- read：允许重放，不需要 provider idempotency key。
- idempotent_write：服务端生成稳定 provider key，跨重试复用；success replay 不得再次调用 provider。
- non_idempotent_write：一旦 dispatch 后结果不确定，只能进入 `ambiguous`，不得自动二次调用。

## Adapter / executor rules

- provider idempotency key 必须在服务端生成并持久化，不能依赖前端或 handler 内随手生成。
- adapter 不得在 handler 内自行 commit；提交由统一 runtime / executor 控制。
- 同一逻辑键并发只允许创建一个 ToolCall，最多一个非幂等 dispatch。
- 本地提交失败但 provider 可能已成功时，非幂等写入必须进入 `ambiguous`，映射为 `waiting_user` / `stopped`，不能自动重试。
- 当前生产 runtime tool 若属于真实发布类动作，在本 Task 内一律显式标记成 `read` 或阻断真实执行，不做外部真发布。

## Required tests

至少覆盖计划 Task 7 的关键场景：

1. active / paused / unknown thread 删除返回 409。
2. terminal / empty thread 可永久删除。
3. 删除与 worker acquire 并发安全。
4. 非 owner（含管理员）删除 404/拒绝。
5. 跨用户子记录导致事务整体冲突，不能部分删除。
6. 正式成果 / BrainTask / Content / Deliverable / formal Event 保留并解链 provenance。
7. 消息、技术日志、owned LLMCall、read ToolCall/Attempt 被删除。
8. write ToolCall/Attempt 保留并解链 provenance。
9. ToolSpec 未声明 side effect 时失败。
10. success replay 不再次调用 provider。
11. 幂等写 timeout 使用同 key 重试。
12. 非幂等写 timeout / commit fail 进入 ambiguous，且不自动二次调用。
13. 同逻辑键并发最多一个 ToolCall / 一个非幂等 dispatch。
14. ambiguous 映射 waiting_user / stopped，而不是 worker 无限重试。
15. 0500 迁移升级/降级通过。

## Files in scope

- `backend/app/services/conversations.py`
- `backend/app/services/runtime_state.py`
- `backend/app/models/brain.py`
- `backend/app/models/__init__.py`
- `backend/app/orchestrator/tool_executor.py`
- `backend/app/orchestrator/runtime_tools.py`
- `backend/app/orchestrator/skill_runtime.py`
- `backend/app/tools/adapter.py`
- `backend/app/tools/__init__.py`
- `backend/migrations/versions/20260730_0500_tool_side_effect_outbox.py`
- `backend/tests/test_conversation_api.py`
- `backend/tests/test_tool_adapter.py`
- `backend/tests/test_runtime_tool_executor.py`
- `backend/tests/test_worker.py`
- `backend/tests/test_migrations.py`

## Out of scope

- 不改 Task 8 前端单 Turn 投影。
- 不做真实外部平台发布。
- 不顺手改 unrelated cost / 旧 UI 问题。
