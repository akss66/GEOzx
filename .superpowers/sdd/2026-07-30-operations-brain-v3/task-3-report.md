# Task 3 报告：Conversation Worker 化与可分类故障

## 状态

已完成。

实现提交：

- `ad28e24 feat: execute conversation turns through durable workers`

## 实现结果

### Conversation API 与 durable Worker

- `POST /brain/conversations/{thread_id}/turns` 现在只执行：
  1. append/reuse ConversationTurn；
  2. claim/reuse AgentRun；
  3. 首次 claim 时将 Run 持久化为 `queued`；
  4. 首次 claim 时提交 `execute_agent_run` 队列任务；
  5. 使用当前持久化 Turn/Run/投影返回 `202`。
- HTTP 请求不再调用 `execute_conversation_turn`，不会等待模型、Query、Skill 或 operation 完成。
- 幂等重放复用相同 Turn/Run，且只有首次 claim 会 enqueue。
- enqueue 失败发生在 Run 已持久化为 `queued` 之后；测试实际调用 `recover_agent_runs`，确认 reconciliation 会重新提交同一 Run。
- Worker 在 legacy `task_not_found` 判断前识别带 `thread_id + turn_id` 的 Conversation Run。
- Worker 使用 `request_payload` 重建 `CreateConversationTurnRequest`，并按同一 org/user/thread/turn 加载执行范围后调用 `execute_conversation_turn`。
- 首次 task-free Conversation 和已有 SkillRun 恢复共用同一 Worker 分支。
- 自然语言路由（`requested_skill_code=None`）重试时复用持久化 SkillRun；显式 Skill code 仍必须匹配，Query 的两个公开别名视为等价。
- 未新增 Turn 状态列、迁移、Task 5 scope 规则或前端改动；未创建重复用户消息。

### 安全且可分类的运行时故障

- 新增 `ProviderRuntimeFailure`，只保存安全信息：
  - HTTP status code；
  - `http` / `timeout` / `connection` / `unknown` 类型；
  - 已脱敏消息或固定通用消息。
- LLM Gateway 的最终 `LLMError.__cause__` 现在保留上述类型化信息，同时不暴露供应商 response body、API key、原始 timeout 文本或候选模型列表。
- `classify_runtime_failure` 可沿 exception chain 识别：
  - `408`、`429`、`5xx`；
  - 连接错误；
  - 请求/响应 timeout；
  - Gateway 安全包装后的等价元数据。
- `409`、`403`、`PermissionError`、Pydantic validation error 保持 terminal。
- Query、SkillRuntime、operation 只重新抛出 retryable infrastructure failure；现有普通工具异常、范围冲突、输入/权限错误继续生成安全、可操作终态。
- Skill 的 retryable failure 会 rollback 当前失败事务但保留此前已提交的 running SkillRun/Task，供 Worker 有界重试恢复。

## TDD 证据

### RED 1：API/Worker 分流

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_conversation_api.py::test_submit_turn_claims_one_owned_run_without_task `
  tests/test_conversation_api.py::test_duplicate_turn_submission_does_not_enqueue_the_same_run_twice `
  tests/test_conversation_api.py::test_queue_submission_failure_keeps_a_durable_queued_run `
  tests/test_worker.py::test_worker_executes_a_task_free_conversation_before_legacy_task_lookup -q
```

实现前结果：`4 failed`。

失败原因分别为 API 内联执行、未 enqueue、enqueue 故障测试未触发，以及 Worker 在 Conversation 执行前落入 legacy `task_not_found`。

实现后结果：`4 passed`。

### RED 2：Gateway 元数据与 retryable 冒泡

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_llm_gateway.py::test_gateway_preserves_safe_retry_metadata_as_the_llm_error_cause `
  tests/test_turn_execution.py::test_query_retryable_infrastructure_failure_bubbles_to_the_worker `
  tests/test_turn_execution.py::test_operation_retryable_infrastructure_failure_bubbles_to_the_worker `
  tests/test_account_inspection_skill.py::test_account_inspection_retryable_infrastructure_failure_bubbles_to_worker -q
```

实现前结果：`6 failed`（Gateway 测试参数化为 429、503、read timeout）。

实现后结果：`6 passed`。

### RED 3：自然语言 SkillRun 恢复

命令：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_worker.py::test_worker_recovers_expired_v2_skill_without_replaying_side_effects -q
```

将一个恢复组合改为 `requested_skill_code=None` 后，实现前结果：`1 failed, 2 passed`；最小修复后：`3 passed`。

## 最终验证

定向回归：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_conversation_api.py `
  tests/test_llm_gateway.py `
  tests/test_worker.py `
  tests/test_turn_execution.py `
  tests/test_account_inspection_skill.py -q
```

结果：`97 passed in 42.39s`。

Ruff：

```powershell
.\.venv\Scripts\python.exe -m ruff check `
  app/api/conversations.py `
  app/worker.py `
  app/llm/gateway.py `
  app/core/runtime_failures.py `
  app/services/agent_runs.py `
  app/services/turn_execution.py `
  app/orchestrator/skill_runtime.py `
  tests/test_conversation_api.py `
  tests/test_llm_gateway.py `
  tests/test_worker.py `
  tests/test_turn_execution.py `
  tests/test_account_inspection_skill.py
```

结果：`All checks passed!`。

其他：

- `git diff --check`：通过。
- 暂存 diff 敏感词检查：只命中验证脱敏行为的伪造测试字符串，没有真实凭据。

## Concerns / 后续边界

- enqueue 调用失败时当前 HTTP 请求会失败，但 durable Run 已是 `queued`，并由 reconciliation 恢复；客户端使用相同幂等键重放不会重复 enqueue。
- Task 4 仍需负责 dead-letter 与 Turn 状态全账本收口；本任务没有提前引入这些状态或迁移。
- 本任务按 brief 只运行指定的定向回归与 Ruff，没有声称执行整个仓库的全量测试。
