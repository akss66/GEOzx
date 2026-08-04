# Task 4C Implementer Report

## Review fix round 4 (latest; supersedes round-3 lock order and Task 6 mapping)

### Outcome

- **One global lock order:** all affected runtime transitions now use
  `ContentItem -> AgentRun -> ConversationTurn -> BrainTask -> parent SkillRun -> child SkillRuns by id -> RunRevision -> Deliverables by id -> AgentToolCalls by id`.
  Discovery is read-only under `no_autoflush`; mutation begins only after the required locks.
  A close scope with no content may legally start at `AgentRun`, but never requests a
  `ContentItem` later in the same transaction.
- **Terminal-ledger immutability:** cancellation and worker failure convergence bind only
  active SkillRuns. If every SkillRun is terminal, the outer AgentRun/RunRevision may
  converge without selecting a completed child. Completed snapshots and completed
  AgentToolCall receipts remain byte-for-byte/status-for-status unchanged across first and
  duplicate cancellation and corrupt-worker recovery.
- **Composite-intent recovery:** an `operation_iteration` root or any durable parent link
  activates strict composite validation. Terminal multi-root, disjoint, cyclic, missing-parent,
  wrong-lineage, and multiple-active-branch graphs fail closed before Skill execution. Multiple
  terminal roots without composite intent retain the legacy `None` recovery result.
- **Nested reject transaction:** finish approval prelocks the entire composite graph and exact
  artifact/tool call. Child rejection, deliverable rejection, parent/run/turn/task blocking,
  approval audit, and durable runtime events commit once in the API-owned transaction.
  Transaction-neutral helpers return scalar-only durable publish envelopes; publication reads
  committed Events and performs no DB commit/write. Event ID, type, and turn must all match, so
  a stale or forged scalar envelope is ignored. Rollback publishes nothing. Same-decision
  retries replay the same durable event IDs for client dedupe; opposite decisions remain 409.
- **Task 6 boundary restored:** the round-3 special-case mapping
  `stopped + TOOL_RESULT_AMBIGUOUS -> BrainTaskStatus.PENDING_CONFIRMATION` was removed from
  the 4C diff. `close_runtime_state` again uses the baseline `brain_task_status(stopped)` mapping.
  The known expectation mismatch belongs to Task 6 and is deliberately not fixed here.

### RED -> GREEN evidence

- Content close initially acquired `Run -> Turn -> Task -> Content -> Skill`; the lock-order
  gate now observes `Content -> Run -> Turn -> Task -> Skill`.
- All-terminal cancel initially changed a completed child to `cancelled`; all-terminal, mixed,
  and duplicate gates now preserve terminal child snapshots and receipts.
- Terminal composite intent with two roots initially returned no recovery owner; resolver and
  real worker gates now fail closed before `execute_conversation_turn`.
- The first real worker gate exposed a second terminal fallback: failure convergence changed the
  newest completed unrelated root to `failed`. Active-only implicit binding fixed it, and the
  gate now proves both terminal snapshots plus the completed receipt are immutable.
- Caller-owned close initially had no publish envelope. The API now publishes only after its
  outer commit; a rollback followed by attempted publication emits nothing.
- Same-decision approval replay initially exposed a production `NameError` because the new API
  branch had not imported `SkillRun`; the import and replay gate are now green.

### Focused and real PostgreSQL evidence

Core lock/recovery/approval/cancel focused matrix, including ordinary approved/rejected finish
approval compatibility:

```text
6 passed in 2.75s
```

Nested reject API commit/replay and crash-before-commit rollback:

```text
2 passed in 1.43s
```

PostgreSQL 16 used two independent `AsyncSession`s, a five-second server lock timeout, and a
15-second overall timeout. The child side performs the real
`write_runtime_deliverable -> close_runtime_state` sequence while its competitor performs accept,
pause, or cancel:

```text
backend/tests/test_composite_skill_runs_postgres.py::test_postgres_child_finish_follows_global_lock_order[accept]
backend/tests/test_composite_skill_runs_postgres.py::test_postgres_child_finish_follows_global_lock_order[pause]
backend/tests/test_composite_skill_runs_postgres.py::test_postgres_child_finish_follows_global_lock_order[cancel]
3 passed in 3.39s
```

Fresh combined run with the existing accept-first/pause-first lost-wakeup gate:

```text
5 passed in 2.81s
```

No schema reset was required. Root deleted the isolated `geozx-task4c-pg-round4` container after
verification.

### Expanded regression and the owned baseline boundary

Unfiltered expanded run:

```text
197 passed, 1 failed in 99.66s
```

The only failure is the intentionally restored Task 6 baseline mismatch:

```text
tests/test_worker.py::test_worker_recovers_expired_v2_skill_without_replaying_side_effects[running-True-True-stopped-TOOL_RESULT_AMBIGUOUS]
```

The runtime correctly persists `AgentRun=stopped`, `SkillRun=stopped`,
`AgentToolCall=ambiguous`, and `error_code=TOOL_RESULT_AMBIGUOUS`. The remaining assertion expects
`BrainTaskStatus.PENDING_CONFIRMATION`, while the restored shared baseline maps `stopped` to
`BrainTaskStatus.FAILED`. That semantic aggregation is Task 6 ownership, not a 4C locking,
recovery, cancellation, or transaction concern.

Fresh expanded run with only that exact parameter node deselected:

```text
197 passed, 1 deselected in 96.20s
```

### Static and diff verification

```text
ruff check <all round-4 changed production/test files>: All checks passed
python -m compileall -q <round-4 production/test files>: passed
```

Targeted mypy follows imports and reports 15 existing diagnostics in nine files. The diagnostics
are in `deterministic_test.py` (4), `data_import/templates.py` (1),
`schemas/conversation.py` (1), `account_data_view.py` (1), `turn_events.py` (1),
`tool_executor.py` (1), `agent_harness.py` (1), the pre-existing optional `TurnEventScope`
arguments in `runtime_state.py` (4), and `brain_runtime.py` (1). No round-4 symbol, syntax, or
lint diagnostic was introduced. `git diff --check` is run again immediately before commit.

Round 4 is committed separately from rounds 1-3. The invalid round-4 lock-map generated against
the wrong checkout is excluded from this commit.

## Review fix round 3 (superseded by round 4 where noted)

### Outcome

- **G1, legal human loop:** production `SkillRuntime()` without a passing critic now pauses visual and calendar children as `needs_review`. Artifact acceptance completes the exact persisted child, wakes the exact persisted `operation_iteration` parent, and repeated acceptance is inert. Publishing still uses its typed `before_finish` approval owner.
- **G2, durable recovery:** worker recovery validates a single composite tree (one `operation_iteration` root, same run/task/thread/turn/org lineage, no cycle, missing parent, disjoint root, or multiple active branch) and resumes the parent. A committed child is reused even when the parent snapshot is stale; provider/tool/expert receipts are not replayed.
- **G3, one lock protocol:** pause and artifact acceptance share `AgentRun -> ConversationTurn -> BrainTask -> ContentItem -> parent SkillRun -> child SkillRuns by id -> Deliverables by id`. Both decisions re-read locked rows. Composite helpers are transaction-neutral; `close_runtime_state(commit=False)` gives the API/runtime caller explicit ownership while preserving the default for legacy callers.
- **G4, cancellation:** cancellation mutates only active parent/child SkillRuns plus the root AgentRun/RunRevision. Completed children and completed tool receipts remain immutable, and duplicate cancellation retains the first terminal timestamp/event set.
- **G5, truthful plan:** paused execution produces no parent operation-plan Deliverable. Exactly one parent plan is written only after all required child nodes are `completed`.
- The shared closure keeps ambiguous external writes at `BrainTaskStatus.PENDING_CONFIRMATION` for the existing `stopped + TOOL_RESULT_AMBIGUOUS` contract.

### Focused and expanded evidence

Focused composite/recovery/cancel matrix:

```text
8 passed in 2.17s
```

Additional lifecycle, hidden-commit, and SQLite lost-wakeup gates:

```text
3 passed, 1 PostgreSQL gate skipped in 1.70s
```

Expanded SQLite-backed regression across operating Skills, worker recovery, turn cancellation, artifact acceptance, and brain approval:

```text
199 passed in 98.8s
```

The lifecycle gate also forces the parent snapshot's first two completed nodes back to `pending`, modeling a crash after child commit and before parent snapshot commit. Recovery reuses the same child SkillRuns and keeps the durable external-call counts unchanged.

### Real PostgreSQL 16 concurrency evidence

Exact nodeids:

```text
backend/tests/test_composite_skill_runs_postgres.py::test_postgres_accept_pause_interleavings_never_lose_wakeup[pause_first]
backend/tests/test_composite_skill_runs_postgres.py::test_postgres_accept_pause_interleavings_never_lose_wakeup[accept_first]
2 passed in 1.53s

backend/tests/test_migrations.py::test_revision_terminal_deliverable_streams_postgres_concurrent_writers
backend/tests/test_migrations.py::test_revision_terminal_deliverable_streams_postgres_reference_matrix_and_atomic_downgrade
2 passed, 6 warnings in 3.69s
```

The pause-first case proves the acceptance transaction blocks behind the shared parent scope and then performs one wake (`parent=running`, `run=queued`). The accept-first case proves the locked approval recheck declines the stale pause (`parent=running`, `run=running`). Both use independent `AsyncSession`s and a ten-second deadlock timeout.

The first local attempt ran `metadata.create_all` before Alembic and therefore left the explicitly named temporary database without an Alembic version. Only `geozx_task4c_r3.public` was reset; all four gates above were then rerun fresh and passed. Root deleted the temporary PostgreSQL container afterward.

### Transaction ownership and static verification

- Runtime spies prove pause performs zero commits; nested finish approval plus parent resume performs zero hidden commits and the simulated outer caller performs exactly one.
- The finish-approval API resolves composite IDs read-only, disables autoflush during discovery, then locks the full scope plus ToolCall before any decision/audit/publish mutation. A SQLAlchemy `before_flush` spy proves no pre-lock flush for both approved and rejected ordinary approvals; both remain compatible.
- A source-level guard covers every function in `composite_skill_runs.py` and rejects any `.commit()` or `.rollback()` call, including reject and resume helpers.
- `Ruff`: all changed production/test files passed.
- `git diff --check`: passed.
- Targeted mypy with imports skipped passed for the new/reworked transaction and recovery modules: `composite_skill_runs.py`, `agent_runs.py`, `skill_approvals.py`, and `worker.py`.
- The broader mypy invocation still reports legacy typing debt in imported modules (55 diagnostics before narrowing the two new resolver diagnostics); the two round-3 resolver diagnostics were fixed. No Task 5/6 files were changed.

Fresh post-review approval/worker/cancel regression:

```text
123 passed in 42.27s
```

Round 3 is committed separately from rounds 1 and 2.

---

## Review fix round 2 (latest, supersedes round-1 C-1 / I-2 / I-3 / I-4 conclusions)

### Outcome

- **C-1, actual child ownership:** `operation_iteration` executes every ready node through its registered real `SkillRuntime` owner. Topic and script run first; visual waits for approved script output; calendar waits for approved visual output; publishing preparation pauses at its real `before_finish` approval. The parent completes only after every required child completes.
- **C-1, durable resume:** child SkillRuns record `composite_parent_skill_run_id`. Artifact acceptance and finish approval recover the same parent/run. Nested closure changes only the child ledger; nested rejection explicitly blocks the parent.
- **I-2:** `cancel_revision_for_run()` converges planned, waiting-predecessor, and running revisions idempotently across AgentRun, parent/child SkillRun, and RunRevision, preserves terminal `finished_at`, and never emits `run.revision_completed` for cancellation.
- **I-3 migration:** 0450 never renumbers historical versions. Upgrade only changes the unique key to `(content_item_id, agent_code, type, version)`. Downgrade performs collision preflight before DDL and fails atomically if the legacy key cannot represent the rows.
- **I-3 stream policy:** reviewed latest/max/newer/supersede/history paths share the explicit `(content_item_id, agent_code, type)` policy. AgentHarness consumes the version returned by persistence.
- Unsupported `generate_strategy` and `requested_output` supplement goals fail with HTTP 422 / `UNSUPPORTED_OPERATION_ITERATION_GOAL` before revision lineage creation.

### I-4 authoritative seven-rule gate

These are the seven rules from the integration brief. They are implemented as seven explicit nodeids in `tests/test_turn_execution.py` and use real `SkillRuntime`; only commit, `_start_skill_stage`, provider/tool/expert, and failure-classification seams are controlled. No test replaces `SkillRuntime.execute`.

| Rule | Nodeid | First / retry / total provider-tool-expert | Durable assertions |
|---|---|---|---|
| 1. Barrier commit failure | `test_i4_rule_1_real_runtime_barrier_commit_failure_has_zero_external_calls` | `0-0-0 / 2-3-2 / 2-3-2` | Failed barrier commit leaves zero durable events; retry starts real runtime only after the new barrier. |
| 2. Barrier committed, step not started | `test_i4_rule_2_real_stage_retry_reuses_committed_revision_plan` | `0-0-0 / 2-3-2 / 2-3-2` | First normalized plan hash is durable and unchanged on retry; invalidations exist, no `step.started` before injected crash. |
| 3. Reused-only revision | `test_i4_rule_3_all_reused_real_runtime_has_only_reused_events_and_zero_external` | `0-0-0 / 0-0-0 / 0-0-0` | All eight contract steps are covered by reused bindings; eight `step.reused`; no invalidated, started, or completed stage event; terminal retry is inert. Incomplete reuse coverage fails closed with `REVISION_REUSE_COVERAGE_INCOMPLETE`. |
| 4. Invalidated before real start | `test_i4_rule_4_invalidated_is_durable_before_real_stage_start_and_external` | `0-0-0 / 2-3-2 / 2-3-2` | Exact invalidation set is durable before `_start_skill_stage`; first attempt has no `step.started`; retry enters the real stage. |
| 5. Durable completed retry | `test_i4_rule_5_durable_completed_skill_retry_does_not_reenter_real_stage` | `0-0-0 / 0-0-0 / 0-0-0` | Real stage seam is forbidden; two calls produce one idempotent `run.revision_completed`. |
| 6. Non-idempotent success, local completion missing | `test_i4_rule_6_non_idempotent_child_success_without_local_completion_goes_manual_once` | `0-1-0 / 0-0-0 / 0-1-0` | A production `AgentToolCall` receipt owned by a real composite child is found through the revision run; retry becomes manual, emits one manual event and no completed event. |
| 7. Terminal duplicate | `test_i4_rule_7_terminal_duplicate_never_classifies_or_reenters_real_runtime` | `0-0-0 / 0-0-0 / 0-0-0` | Failure classification and real stage entry are forbidden; duplicate calls return the persisted terminal result with no event or external call. |

Fresh authoritative gate:

```text
uv run pytest tests/test_turn_execution.py -q -k "test_i4_rule_"
7 passed, 76 deselected in 3.55s
```

The separate `test_operation_iteration_real_runtime_child_lifecycle_retry_gate` is additional composite-child evidence only; it is not counted as any of the seven authoritative rules.

### Canonical stream, child execution, and cancellation nodeids

Canonical `(content_item_id, agent_code, type)` stream gates:

```text
tests/test_deliverable_actions_api.py::test_action_latest_gate_is_scoped_to_the_source_agent_stream
tests/test_approval_workspace_api.py::test_gate_preview_latest_deliverable_uses_explicit_agent_stream
tests/test_publishing_service.py::test_publish_staleness_is_scoped_to_the_approved_agent_stream
tests/test_artifacts_api.py::test_artifact_revision_and_supersede_are_scoped_to_source_agent_stream
tests/test_orchestrator.py::test_rerun_stage_supersedes_old_version
tests/test_agent_harness.py::test_harness_runs_positioning_with_one_account_without_project
```

The final two gates seed another agent's same-type v1/v2 stream and assert engine supersede, AgentHarness acceptance version, and brain history remain scoped to the owning agent.

Real child / goal gates:

```text
tests/test_operating_skills.py::test_operation_iteration_real_runtime_child_lifecycle_retry_gate
tests/test_turn_steering.py::test_unsupported_goal_supplements_are_rejected_before_revision_lineage[...]
```

Cancellation convergence gates:

```text
tests/test_turn_execution.py::test_revision_pre_acquire_cancel_converges_three_ledgers_without_completion_event[queued-planned]
tests/test_turn_execution.py::test_revision_pre_acquire_cancel_converges_three_ledgers_without_completion_event[waiting_predecessor-waiting_predecessor]
tests/test_turn_execution.py::test_running_revision_cancelled_error_hook_is_idempotent_and_never_completes
```

### Migration and regression evidence

SQLite-backed expanded gate:

```text
243 passed, 4 skipped, 1 warning in 78.77s
```

All four skips are explicit PostgreSQL environment gates in the default SQLite run:

```text
tests/test_migrations.py::test_revision_terminal_deliverable_streams_postgres_concurrent_writers
  TEST_POSTGRES_URL is required for the PostgreSQL concurrent writer gate
tests/test_migrations.py::test_revision_terminal_deliverable_streams_postgres_reference_matrix_and_atomic_downgrade
  TEST_POSTGRES_URL is required for the PostgreSQL reference matrix gate
tests/test_migrations.py::test_run_revision_stage_checkpoint_postgres_gate
  TEST_POSTGRES_URL is required for the PostgreSQL migration gate
tests/test_migrations.py::test_turn_steering_postgres_upgrade_and_downgrade_gate
  TEST_POSTGRES_URL is required for the PostgreSQL migration gate
```

The first two are round-2 gates and were also executed against the temporary real PostgreSQL 16 database as shown below. The latter two are pre-existing PostgreSQL-only migration gates; their skip is expected when `TEST_POSTGRES_URL` is absent.

Real PostgreSQL 16 gates (temporary database removed after verification):

```text
tests/test_migrations.py::test_revision_terminal_deliverable_streams_postgres_reference_matrix_and_atomic_downgrade
tests/test_migrations.py::test_revision_terminal_deliverable_streams_postgres_concurrent_writers
2 passed, 6 warnings in 1.11s
```

The PostgreSQL reference matrix runs actual Alembic 0400 -> 0450, preserves exact referenced IDs and versions across acceptance/action/shoot/schedule/checkpoint/SkillRun/AgentToolCall/Event snapshots, proves compatible downgrade/re-upgrade, and proves incompatible downgrade fails before DDL while data and the four-column constraint remain intact.

### Static and diff verification

```text
Ruff: All checks passed
git diff --check: passed
```

Targeted mypy reports 26 pre-existing errors in six legacy files. Exact locations:

```text
app/orchestrator/agent_harness.py:965
app/services/runtime_state.py:310,311,312,313
app/orchestrator/skill_runtime.py:630,888,891,1495,1628,1630,1888,1915,1982,2393,2809,2836,2855,2909,2943,2981,3020
app/services/artifacts.py:265
app/services/deliverable_actions.py:108,120
app/services/turn_execution.py:1803
```

These are unchanged lines or existing typing-debt regions; the round-2 production additions introduce no reported error. The executable wrapper is blocked by local Windows policy, so verification used `uv run python -m mypy`.

Round 2 is committed separately from round 1.

---

日期：2026-08-04
执行者：`/root/task4c_worker`
状态：4C review fix round 1 已实现并验证；下方初版记录后附本轮 superseding evidence

## 交付结果

- supplement control run 仍只做接收确认；可证明的非空 supplement 在同一 caller-owned transaction 内创建独立、task-bound 的 revision `AgentRun`、revision `SkillRun` 与 `RunRevision`，并把 `revision_run_id` / `revision_id` / `task_id` 写回 steering ack。
- empty diff（确定性的“保持现有要求不变”等）不创建 revision、不 enqueue。
- 已知自由文本字段复用 `extract_structured_constraints()` 的 server rule 映射到 `ConstraintPath`；无法证明具体路径的文本使用稳定 unknown marker，交给 dependency planner 安全 full recompute，不由 LLM 猜字段。
- revision run 即使拥有 thread/turn lineage，也通过显式 `operation=execute_revision` 走 task-mode worker 分支；active predecessor 时使用既有 `waiting_predecessor`，terminal 后使用既有 oldest-first promotion。
- `queue_agent_run_behind_task_record()` 提供 transaction-neutral 变体；旧 wrapper 仍负责原有 commit，supplement seam 不含隐藏 commit。
- API 只在 outer commit 后派发 revision。首次 enqueue 失败返回 `dispatch_deferred=true`，durable run 保持 queued；同请求重放只重派同一 revision run，不重复创建 lineage。
- reuse barrier 在任何 revision `skill_runtime.execute()` 之前执行并 commit。manual verdict 直接返回 stopped，外部调用为 0。
- generic partial verdict 使用 `load_latest_stage_output()` hydrate，并只发 `step.reused`，不为 reused step 发 `step.completed`。
- `step.invalidated`、`step.reused`、`run.revision_planned|fallback|manual_reconciliation|completed` 已加入安全 public allowlist。
- `runtime_deliverables` 现在锁 content stream、由服务读取 durable max version 并分配 next version；同一 SkillRun 的精确重放返回原 Deliverable，不重复增版或重复发事件。旧 caller 的 `version` 参数仅为兼容输入，服务完全忽略，不再拥有版本决策权。

## 真实 boundary 与 fail-closed 结论

- parent `operation_iteration` 当前唯一真实 native boundary 是 `prepare_deliverable`。
- logical graph 的 child owner 只在对应 code 真实存在于 `skill_registry` 时成立。
- 当前 `quality_review` 没有真实 child Skill，因此生产 `operation_iteration` revision 会在 barrier 内以 `invalid_graph:executor_boundary_missing` 安全降级为 full recompute。
- 本次实现保留并测试了未来真实 child checkpoint materialize 后的 generic partial/hydrate 通道，但**不能宣称当前生产 operation_iteration 已普遍启用局部恢复**。
- 没有由 parent run 伪造 logical `step.completed` 或虚构 checkpoint stage。

## TDD 证据

按 brief 顺序完成 RED → GREEN：

1. structure：缺 runtime boundary resolver 的 import RED；实现后 2 passed。
2. supplement seam：非空 supplement 无 `RunRevision` RED；实现 empty/non-empty、same-tx lineage、after-commit dispatch 后 2 passed。
3. worker/promotion：turn-bound revision 被误判 conversation-run RED；显式 task-mode branch 后 1 passed。
4. barrier/hydrate/manual：stub `REVISION_EXECUTION_NOT_WIRED` 产生 3 RED；接线后 3 passed。
5. deliverable version：服务仍要求 caller `version` 的 TypeError RED；服务端分配与重放幂等后 1 passed。
6. dispatch retry：第二次请求重复插入 revision AgentRun 的 unique violation RED；按 revision turn/source/task 复用 durable lineage 后 GREEN。

Focused command：

```text
uv run pytest tests/test_revision_runtime_integration.py \
  tests/test_conversation_api.py::test_supplement_creates_immutable_steering_turn_without_cancelling_target \
  tests/test_conversation_api.py::test_nonempty_supplement_creates_task_revision_and_dispatches_after_commit \
  tests/test_conversation_api.py::test_empty_supplement_diff_keeps_control_only_and_creates_no_revision \
  tests/test_worker.py::test_worker_treats_turn_bound_revision_as_task_mode_and_promotes_successor \
  tests/test_turn_execution.py::test_revision_barrier_precedes_external_and_terminal_retry_is_exactly_once \
  tests/test_turn_execution.py::test_revision_partial_hydrates_reused_output_without_completed_event \
  tests/test_turn_execution.py::test_revision_manual_reconciliation_stops_with_zero_external_calls \
  tests/test_runtime_scope.py::test_runtime_deliverable_allocates_next_version_and_replays_same_skill_write -q

10 passed in 3.66s
```

服务/事件/队列/checkpoint 回归：

```text
106 passed in 33.44s
```

较宽的 13-file integration 回归：

```text
256 passed, 1 failed in 79.10s
```

唯一失败可隔离稳定复现，且不是 4C diff 引入：

- `test_worker_recovers_expired_v2_skill_without_replaying_side_effects[...]`
- run / turn / SkillRun 均正确 stopped，错误码为 `TOOL_RESULT_AMBIGUOUS`；但当前 HEAD 的 `runtime_state.brain_task_status("stopped")` 固定返回 `BrainTaskStatus.FAILED`，测试期望 `PENDING_CONFIRMATION`。
- 该 owner 位于 4C 文件范围外，未越权修改；这也是 task aggregation/manual stop 后续必须统一的已知基线缺口。

静态检查：

```text
Ruff（全部 4C modified files）：All checks passed
git diff --check：passed
mypy app/services/runtime_deliverables.py app/services/turn_steering.py app/worker.py：
Success: no issues found in 3 source files
```

对全部 8 个 modified production files 的 mypy 仍报告当前 HEAD 已存在的问题（`skill_runtime.py`、`turn_events.py`、`turn_execution.py` 的旧错误）；本次新增 `turn_steering` union narrowing 和 worker task narrowing 已清零，未伪报全量 mypy 通过。

## 关注点

- source target 尚未拥有 source task + `operation_iteration` SkillRun 时，无法合法满足 `RunRevision` exact lineage/FK；当前保持 control ack，不伪造 BrainTask 或第二套 revision 状态机。真正的 revision seam 仅在完整 source lineage 上启用。
- production partial reuse 受真实 child boundary materialization 限制；当前 quality boundary 缺失，因此 full fallback 是正确、保守的行为。
- `RunRevision` schema 没有 stopped/manual-wait 状态；manual owner 仍由既有 `mode=manual_reconciliation` + task/run stopped 组合表达。task 聚合对 stopped 的基线映射冲突见上。
- 未 commit、未 push、未创建 PR。

---

## Review fix round 1（supersedes 上述已过时关注点）

### Finding 对照

- **C-1**：supplement 由 server extractor 归一化后，将 `days -> cycle_days`、`topic_count -> topic_count`、`duration_seconds -> script_duration_seconds` 与 source frozen input 合并，并在 lineage caller transaction 内同时写入 revision `AgentRun.request_payload.structured_input` 和 revision `SkillRun.input_snapshot`。`operation_iteration` typed input 增加对应可选字段，真实 composite executor 将周期/选题数传给 topic child、脚本时长传给 script child。行为门证明“选题周期仍为 7 天，脚本 30 秒”得到 frozen `{cycle_days: 7, script_duration_seconds: 30}`，产物中的 `script_generation.input` 为 `{duration_seconds: 30}`。同时修正 duration regex 贪婪匹配把 `30` 错抽成 `0` 的问题。
- **I-1**：`plan_hash` 从 revision public allowlist 和 planned/fallback/manual/completed 生产 payload 全部移除；list 与 SSE 均在 read boundary 再次 sanitizer，测试以 legacy raw row 注入 `plan_hash`、input/output snapshot、snapshot hash，公共结果只保留 public IDs/status/reason。
- **I-2**：RunRevision 增加 `blocked`、`stopped`、`manual_reconciliation` 终态及 finished-at lifecycle 约束；新增唯一 owner transition `finish_revision()`。manual、blocked、failed、stopped、terminal exception 路径均同步 RunRevision；worker closure 后 AgentRun/SkillRun/RunRevision 三表状态行为门通过。manual 使用明确 `manual_reconciliation`，没有伪装 `completed`。terminal exception 在写 failed 前先 rollback 未完成业务写，只保留 barrier 后已 durable 的 running fact。
- **I-3**：canonical stream 统一为 `(content_item_id, agent_code, type)`；模型 unique、max-version query 与新迁移 `20260804_0450` 一致。迁移 upgrade 先移除旧 unique，再按新 stream 用 window function 重编号历史数据并创建四列 unique；downgrade 先按旧 stream 重编号再恢复三列 unique，并将新 revision terminal status 安全映射为 failed。cross-agent 各 v1、同 agent v1→v2、exact replay 不新增 Deliverable/event 均有行为断言。
- **I-4**：wrapper 级 durable retry 门覆盖：barrier commit 前 crash（0 external）；barrier durable 后 `_start_skill_stage` 前 crash（0 external）；reused step 只发 `step.reused` 且不伪发 completed；invalidated durable 后 `_start` 前仍 0 external；terminal duplicate 不重调；non-idempotent external success 而 SkillRun local completion 未 durable 时，retry 读取 receipt 转 manual，provider 总调用数 1；SkillRun completed 而 RunRevision completion 未 durable 时，retry 只补 revision terminal/event，不重调 executor。terminal executor error 另验证 RunRevision failed。
- **M-1**：barrier transaction 在所有 fallback/policy resolution 后，按最终 durable `affected_steps` 幂等追加 `step.invalidated`。full fallback 扩出的全图 steps 与事件 projection 集合完全一致，terminal replay 不重复。

### 迁移与 PostgreSQL 并发门

SQLite migration gate：

```text
pytest tests/test_migrations.py::test_migration_head_is_revision_terminal_deliverable_streams \
  tests/test_migrations.py::test_revision_terminal_deliverable_streams_sqlite_upgrade_and_downgrade -q
2 passed
```

真实 PostgreSQL 16 gate（临时库由 root 提供并在验证后删除）：

```text
$env:TEST_POSTGRES_URL='postgresql+psycopg://geozx:geozx@127.0.0.1:55443/geozx_task4c'
pytest tests/test_migrations.py::test_revision_terminal_deliverable_streams_postgres_concurrent_writers -q
1 passed in 3.11s
```

该门实际 Alembic upgrade 到 `20260804_0450`，并用两个独立 AsyncSession 并发调用生产 `write_runtime_deliverable()`：第二 writer 在 ContentItem `FOR UPDATE` 上阻塞；同 agent 返回 `[1, 2]`，cross-agent 返回 `1`。

### Fresh verification

Focused review-fix matrix：

```text
22 passed, 1 warning in 7.04s
```

Expanded（9 files）：

```text
252 passed, 3 skipped, 1 failed in 76.22s
```

唯一失败是明确排除、未由本 diff 引入的 Task 6 baseline：

```text
tests/test_worker.py::test_worker_recovers_expired_v2_skill_without_replaying_side_effects[
  running-True-True-stopped-TOOL_RESULT_AMBIGUOUS
]
```

失败原因：该非 revision recovery 路径的 AgentRun/SkillRun 正确 stopped 且 error code 为 `TOOL_RESULT_AMBIGUOUS`，但现有 `runtime_state.brain_task_status("stopped")` 将 BrainTask 映射为 `FAILED`，测试期望 `PENDING_CONFIRMATION`。这是 Task 6 owner；本轮没有修改 BrainTask baseline。

静态与 diff：

```text
Ruff（17 个 round1 modified code/test files）：All checks passed
mypy（8 个本轮可独立检查的 production files）：Success: no issues found
git diff --check：passed
```

额外全量 modified mypy 仍只有旧 baseline：`turn_events.py:199` 的 readonly protocol 不匹配、`turn_execution.py:1767` 的旧 `str | None -> str`；本轮新增的 terminal status typing 已清零。

### Diff 边界 / 明确未做

- 新增迁移只有 `20260804_0450_revision_terminal_deliverable_streams.py`，线性接在 `0400`；当前没有 `0500`，因此无需重写后续 down-revision。若 root 后续引入 `0500`，其 parent 必须指向 `0450`。
- 未修改 Task 6 BrainTask stopped/ambiguous aggregation baseline。
- 未加入 Task 5 brief、未加入依赖、未 push/未创建 PR。
- Root 授权后创建 review-fix commit：`7aae77ef5fc2c813cd2eb8e517793fe5db358412`。
