# Task 7 implementation report

## Delivered

- Reworked permanent conversation deletion into a lock-first, fail-closed boundary.
  It locks Thread, Turn, AgentRun, SkillRun, Invocation, ToolCall, Attempt, and
  referenced Task rows before evaluating ownership and lifecycle state.
- Only the exact conversation owner can delete. Active, paused, ambiguous, and
  unknown states return the stable `CONVERSATION_DELETE_BLOCKED` 409 response;
  empty or fully terminal conversations can be deleted.
- Deletion now preserves BrainTask, Content, Deliverable, formal Event, and all
  write ToolCall/Attempt audit rows while clearing nullable runtime provenance.
  Conversation messages, technical events, read ToolCall/Attempt rows, and only
  owner-scoped LLM calls tied to the conversation/AgentRun/Invocation are deleted.
  The previous task-wide LLM deletion was removed.
- Added mandatory `ToolSpec.side_effect_level`: `read`, `idempotent_write`, or
  `non_idempotent_write`. Every production runtime tool is explicitly classified
  as `read`; no external publish operation was enabled.
- Added server-generated stable provider idempotency keys for writes, persistent
  `ToolExecutionAttempt` outbox rows, durable dispatch state, success replay, and
  retry reuse of the same provider key.
- Non-idempotent timeout and post-provider local commit failure converge to
  `ambiguous`. Replays never dispatch them again.
- Recovery recognizes the crash window represented by
  `ToolCall=running + Attempt=dispatched`, promotes both ledgers to `ambiguous`,
  and stops Run/SkillRun for explicit user resolution instead of retrying.
- Removed the adapter's internal commit; the durable executor owns all transaction
  boundaries and refuses direct write execution without a server provider key.
- Added reversible migration `20260730_0500_tool_side_effect_outbox`.

## Inherited half-change audit

- Kept and hardened: the ToolSpec side-effect contract, runtime read
  classifications, provider-key skeleton, ToolExecutionAttempt model, adapter
  no-commit behavior, and the initial tool/delete regression tests.
- Reworked: the deletion implementation (it only checked Run/SkillRun, deleted all
  ToolCalls, and deleted LLM calls by shared Task id), collision replay validation,
  local commit-failure recovery, and ambiguous runtime convergence.
- Added: the missing 0500 migration, Invocation/Attempt deletion guards, exact
  LLM ownership predicates, write-audit detachment, provider-key enforcement,
  crash-after-dispatch recovery, and migration/head tests.

## TDD evidence

- RED: a `running` ConversationTurn was deleted with HTTP 204.
- GREEN: active/paused/unknown descendants now block deletion and the complete
  conversation suite passes.
- RED: the 0500 migration module was missing.
- GREEN: SQLite upgrade/downgrade preserves legacy calls as `read`, creates the
  Attempt outbox, and reverses both additions.
- RED: a non-idempotent local result-commit failure expired ORM ids and could not
  persist `ambiguous`.
- GREEN: stable ids are captured before commit; ToolCall/Attempt become ambiguous
  and replay performs no provider call.
- RED: direct write adapter invocation accepted no provider idempotency key.
- GREEN: all writes require the server-generated key.
- RED: worker recovery treated `running + dispatched` non-idempotent work as a
  normal failed skill.
- GREEN: it is promoted to ambiguous and stopped without dispatch replay.

## Verification

- Directed Task 7 and affected regression suite:
  `150 passed, 3 warnings in 62.40s`.
- Migration smoke test: `1 passed`; upgrade and downgrade verified on SQLite.
- Ruff on all changed backend/test/migration files: passed.
- `alembic heads` -> `20260730_0500 (head)`.
- Python compileall and `git diff --check`: passed.

## Concerns / follow-up

- The three test warnings are pre-existing asyncpg connection-cancellation resource
  warnings caused by parallel isolated specialist sessions in the test harness;
  no assertion failed and Task 7 does not change that session factory.
- Real external publishing remains intentionally unavailable. Before adding one,
  its provider adapter must actually forward the persisted provider key and expose
  an operator reconciliation path for ambiguous attempts.
- SQLite ignores row-level `FOR UPDATE`; the deletion/worker race is enforced by
  the emitted PostgreSQL locking statements and the shared lock order, while unit
  tests validate the lifecycle decision logic.
