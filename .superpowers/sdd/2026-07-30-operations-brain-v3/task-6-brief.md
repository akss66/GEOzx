# Task 6 Brief — Skill 恢复冻结、专家编排与按需质量门

## Ownership

负责计划 Task 6 列出的 Worker、SkillRuntime/SkillDefinition/Registry、专家 Harness、0400 迁移和定向测试。你不是代码库唯一执行者；不得回退其他改动，必须复用 Task 5 的 RuntimeScope 与成果来源字段。

## Workflow

1. 严格 TDD，先记录 RED。
2. 先完成版本/输入冻结与恢复冲突，再做专家并行，最后做 Critic/正式成果边界。
3. 不得在共享 `AsyncSession` 上 `asyncio.gather`。
4. 完成后跑定向测试、Ruff、Alembic heads/迁移编译、`git diff --check`，提交代码和强制加入 Task 6 报告。

## Frozen recovery

- `SkillRun` 增加 64 位 `input_hash`，哈希 Pydantic 规范化后的完整冻结快照（包含 account_id），使用稳定 JSON 和 SHA-256。
- 恢复时必须显式使用持久化 `skill_code + skill_version + input_snapshot + input_hash`；不得先读取 Registry 最新版本。
- 新的逻辑幂等键为 `skill:{code}`，不含版本；版本是执行记录属性，不是另一个逻辑执行槽。
- 兼容旧 `skill:{code}:vN`，但同一 run/code 有多个非终态候选时返回 `SKILL_RECOVERY_AMBIGUOUS`。
- 同键不同 code/input_hash，或并发 winner 的 version/hash/RuntimeScope 任一不同，返回不可重试冲突。
- 快照重算 hash 不一致返回 `SKILL_INPUT_INTEGRITY_MISMATCH`。
- 精确版本不存在返回 `SKILL_VERSION_UNAVAILABLE`，不能回退最新版本。
- Worker 只查 running/retry_wait/waiting_permission 等可恢复状态，不得按 ID 倒序选择任意记录。

## Migration 0400

- 先 nullable 添加 `input_hash`，按历史平铺/新版嵌套 snapshot 规范化回填，再验证 64 位并改 non-null。
- 发现坏快照、同 run/code 多个非终态记录、无法唯一恢复的版本时 preflight 整体失败。
- 不批量重写旧幂等键，避免碰撞。
- Registry 同时保留仍有可能恢复的历史版本。

## Safe expert stages

`SkillDefinition` 增加：

```python
expert_stages: tuple[tuple[str, ...], ...]
critic_policy: Literal["none", "required"]
```

- `expert_stages` 扁平化必须与 `expert_codes` 完全一致。
- 同一 stage 内每个专家使用独立 `async_session()`，通过 ID 重载 User/Task/Scope，返回不可变 DTO；不得传跨 session ORM 对象。
- 并发上限 3，使用 `gather(return_exceptions=True)` 收口整个 stage。
- 结果按 `expert_codes` 定义顺序汇总，不按完成顺序。
- 同 stage 专家只收到冻结输入、相同证据、已完成前序 stage 输出；不能看到同 stage 未完成输出。
- 任一专家失败：保留 Invocation 审计，SkillRun failed/blocked，不创建正式 Deliverable。

## Trace authority

- trace-only 输出权威存入对应 `AgentInvocation.upstream["trace_only_output"]`。
- `_existing_trace_result` 先读 Invocation；旧 `AgentRun.result_payload["trace_only_outputs"]` 仅作历史 fallback。
- 独立会话入口返回 DTO，例如 `AgentTraceResult(invocation_id, agent_code, output_summary, output)`。

## Critic and formal artifact boundary

- Critic 只能从 SkillRuntime 正式成果 finalize 阶段触发。
- `critic_policy="none"` 必须零调用；ANSWER/QUERY 路径不得创建 Deliverable/AgentQualityScore，不调用 Critic。
- required policy 沿用最多两轮修订；不合格后进入待人工审核，不能自动采用。
- 正式成果必须有已完成的 producer Invocation：
  - producer agent_code 属于 definition.expert_codes；
  - producer 不是 `00-decision`；
  - Invocation 属于当前 RuntimeScope；
  - output 通过 definition.output_model；
  - required Critic 已通过或显式进入人工审核。
- Deliverable 写入 producer 的 agent_code 和 Task 5 已有的 Invocation 来源字段。
- 专家失败时，主 Agent只能回复失败/下一步，不能调用内部 report builder 冒充专家生成正式成果。

## Required tests

至少覆盖计划 Task 6 的 18 类测试：

1. v1 执行中注册 v2，恢复仍使用 v1。
2. 调用默认输入不同，恢复仍使用持久化输入。
3. 精确版本缺失时阻塞。
4. snapshot/hash 篡改拒绝。
5. 同幂等键不同输入拒绝。
6. 并发 winner 的 version/hash/scope 不同拒绝。
7. 旧 versioned key 恢复。
8. 多个非终态 legacy run 返回歧义。
9. 同 stage 专家时间重叠且 session 独立。
10. 并行结果按定义顺序。
11. 所有 Invocation trace 可恢复、无 JSON 丢失。
12. stage 输入隔离。
13. 任一专家失败时 Deliverable=0。
14. 正式成果 producer 不是 `00-decision`。
15. required 调 Critic、none 零调用。
16. ANSWER/QUERY 零 Critic、零正式成果。
17. 0400 hash 回填与 downgrade。
18. 迁移 preflight 冲突整体回滚。

## Out of scope

- 不修改 Task 7 的写 Tool outbox/exactly-once。
- 不修改 Task 8 前端。
- 不做线上部署。
