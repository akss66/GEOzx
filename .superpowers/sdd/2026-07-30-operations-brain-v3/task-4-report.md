# Task 4 Report: 统一 Turn/Run/SkillRun/Task 状态机

## Status

Implemented and committed.

- Code commit: `b14d923 fix: converge runtime state across turn ledgers`
- Migration head: `20260730_0200`
- Scope respected: no Task 5 source FK, Task 6 Skill versioning, frontend, or unrelated runtime feature work.

## TDD evidence

RED was established before production implementation:

```text
uv run pytest tests/test_turn_execution.py -k close_runtime_state -q
ModuleNotFoundError: No module named 'app.services.runtime_state'
```

The migration test was also RED before the revision existed:

```text
uv run pytest tests/test_migrations.py -k runtime_state_convergence -q
ModuleNotFoundError:
  migrations.versions.20260730_0200_runtime_state_convergence
```

GREEN coverage added:

- Parameterized four-ledger mapping for `completed`, `failed`, `dead_letter`,
  `cancelled`, and `waiting_permission`.
- `retry_wait` keeps the Turn response empty and creates no terminal message.
- Replayed `failed` and `cancelled` closure writes exactly one
  `brain.runtime.message_done`.
- Cross-scope SkillRun ownership is rejected and rolls back without partially
  updating Turn, Run, or Task.
- Migration normalizes legacy aliases and unknown values before adding checks.

## Implementation

### Exact state sets

- ConversationTurn:
  `queued`, `running`, `retry_wait`, `waiting_permission`,
  `waiting_decision`, `waiting_user`, `completed`, `blocked`, `failed`,
  `dead_letter`, `cancelled`, `stopped`.
- AgentRun: the Turn set plus `claimed` and `waiting_predecessor`.
- SkillRun:
  `running`, `retry_wait`, `waiting_permission`, `completed`, `blocked`,
  `failed`, `cancelled`, `stopped`.

All three ORM tables and migration `0200` carry matching named
`CheckConstraint`s.

### Mapping

| Runtime state | Turn | Run | SkillRun | BrainTask |
| --- | --- | --- | --- | --- |
| active / retry | active state | active state | `running` or `retry_wait` | `RUNNING` |
| waiting | waiting state | waiting state | `waiting_permission` | `PENDING_CONFIRMATION` |
| `stopped` | `stopped` | `stopped` | `stopped` | `PENDING_CONFIRMATION` |
| `completed` | `completed` | `completed` | `completed` | `COMPLETED` |
| `blocked` / `failed` / `cancelled` | same | same | same | `FAILED` |
| `dead_letter` | `dead_letter` | `dead_letter` | `failed` | `FAILED` |

`dead_letter` maps SkillRun to `failed` because the brief's exact SkillRun set
does not permit `dead_letter`.

### Central close

`close_runtime_state(session, *, scope, status, message, error_code=None)` now:

1. locks AgentRun, Turn, optional SkillRun, and optional BrainTask;
2. validates organization/thread/turn/run/task ownership before mutation;
3. applies the ledger-specific mapping and event projection;
4. commits all ledger and durable event changes once;
5. rolls back the whole transaction on validation or persistence errors.

First terminal state wins. Replays use stable event idempotency keys, preserve
an existing successful/formal response and Skill output, and do not append a
second terminal message. Paused states may later converge to a terminal state.

Worker acquire/complete/failure/cancel, task-free delivery, query Skill close,
composite Skill start/pause/finish/failure, interrupted Skill recovery, and
operation close now use the service. The old task-free/operation delivery
methods are no longer called from `turn_execution.py`.

### Migration

`20260730_0200_runtime_state_convergence.py` is based on `20260730_0100`.

- Adds indexed `conversation_turns.status`, defaulting to `queued`.
- Historical Turn with non-null `assistant_response` becomes `completed`.
- Turn without a response maps recognized current/legacy Run states; unknown or
  missing Run state becomes `queued`.
- Legacy Run aliases such as `retry_scheduled`, `waiting_approval`, `done`,
  `error`, and `canceled` are normalized. Unknown Run values become `failed`
  rather than being re-executed.
- Skill waiting aliases become `waiting_permission`; Skill `dead_letter` and
  unknown values become `failed`.
- Check constraints are created only after normalization.

## Verification

Fresh final targeted verification:

```text
uv run pytest \
  tests/test_agent_runs.py \
  tests/test_turn_execution.py \
  tests/test_account_inspection_skill.py \
  tests/test_migrations.py \
  tests/test_worker.py::test_worker_recovers_expired_v2_skill_without_replaying_side_effects \
  tests/test_worker.py::test_worker_409_conflict_finishes_run_and_task_once \
  tests/test_conversation_api.py::test_owner_can_permanently_delete_conversation_and_execution_logs \
  tests/test_conversation_api.py::test_task_free_turn_broadcasts_incremental_response_events \
  tests/test_conversation_api.py::test_true_duplicate_returns_the_same_turn_and_run \
  tests/test_conversation_api.py::test_blocked_turn_still_projects_called_experts -q

89 passed, 1 warning in 22.98s
```

The warning is Alembic's existing `prepend_sys_path` configuration deprecation.

```text
uv run ruff check <Task 4 production and test files>
All checks passed!

git diff --check
exit 0

uv run alembic heads
20260730_0200 (head)

uv run alembic upgrade 20260730_0100:20260730_0200 --sql
exit 0
```

The full backend suite was run before the final compatibility fixes:

```text
884 passed, 8 failed, 4 warnings in 277.73s
```

Seven Task 4-related failures were then fixed and are included in the fresh
89-test GREEN command above. The one remaining independently reproduced failure
is listed below.

## Concerns

1. Live `uv run alembic upgrade head` did not reach Task 4. The connected
   development database is stamped `20260728_0100`, but its
   `conversation_threads` table lacks the
   `uq_conversation_thread_id_org (id, org_id)` constraint declared by that
   already-applied migration. Upgrade therefore fails in pre-existing
   `20260728_0200_skill_runs` while creating its composite FK. PostgreSQL
   transactional DDL rolled the attempt back; `alembic current` remains
   `20260728_0100`. The Task 4 migration itself passes SQLite upgrade/downgrade
   coverage and PostgreSQL offline SQL compilation.
2. `tests/test_main_agent_v2_flow.py::
   test_main_agent_v2_cross_intent_flow_preserves_turn_ownership` still fails
   independently at `inspection_artifact is not None`. The API POST enqueues
   six worker jobs, and the test immediately queries the database without
   executing or awaiting those jobs. No Task 4 stack or state assertion is
   involved. This out-of-scope asynchronous test-environment issue was not
   papered over by changing unrelated files.
