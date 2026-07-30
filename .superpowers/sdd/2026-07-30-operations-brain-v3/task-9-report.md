# Task 9 implementation report

## Delivered

- Added five nullable, nonnegative per-Turn telemetry fields:
  `route_ms`, `first_token_ms`, `completion_ms`, `total_ms`, and
  `model_call_count`.
- Kept historical rows unmodified. Migration `20260730_0600` has no database
  defaults or backfill. New ORM-created Turns initialize
  `model_call_count = 0`.
- Added `turn_observability.py` with a bounded `ContextVar` carrying only
  organization, thread, Turn, Run, and timing state.
- Bound the complete non-terminal Turn execution to the observability scope:
  - T1 is recorded when the Worker starts real Turn execution;
  - route latency is first-write-wins;
  - only the first non-empty `00-decision` provider delta records TTFT;
  - expert and system-animation deltas do not record user TTFT;
  - completion and total duration are updated by the runtime closure
    transaction;
  - retry/resume preserves route/TTFT and may update completion/total.
- Counted model attempts next to the durable `LLMCall` audit write. Provider
  fallback attempts each increment atomically; terminal replay returns the
  persisted result without calling the Gateway.
- Enforced model budgets with audit evidence:
  - deterministic greeting/capability: router 0, answer 1;
  - explicit query/Skill: router 0;
  - fuzzy answer: router 1, answer 1.
- Fixed deterministic negation parsing so
  `只查询账号数据，不生成策略` cannot be misread as a positive strategy
  generation request.
- Replaced loose technical metadata with an allowlisted execution summary:
  public route, Skill, expert, tool, quality, duration, retry, side-effect,
  artifact/evidence, error-code, and recovery fields only.
- Added conditional Turn timing/model metrics to the existing collapsed
  technical details. No legacy chat renderer or global execution drawer was
  restored.
- Removed a per-tool retry-count N+1 query found during final review by loading
  all attempt counts with one grouped query.
- Added the ten-case browser matrix covering greeting, capability, query,
  account inspection, review, topic planning, publish preparation, real
  publish permission, forced expert failure, and same-thread follow-up.
- Made Playwright verification deterministic:
  - CI cannot reuse an unrelated server already bound to port 5173;
  - the dev-server command is cross-platform;
  - worktree junction-backed dependencies are inside Vite's explicit allow
    list;
  - assertions use `expect.poll`, semantic controls, and no sleeps.
- Split CI into directed V3, full backend, temporary PostgreSQL migration,
  frontend, and Playwright lanes with explicit timeouts.
- Added the V3 rollout, alerting, application rollback, and exceptional
  database rollback runbook.
- Offline Alembic mode now fails explicitly with `CommandError` for the
  data-dependent migration chain instead of pretending to emit a complete SQL
  artifact.

## TDD and contract evidence

- RED: nullable timing fields and nonnegative constraints did not exist.
  GREEN: model and migration tests cover `NULL`, ORM zero initialization, and
  all five negative-value constraints.
- RED: fallback calls produced multiple `LLMCall` rows without Turn-level
  attempt telemetry.
  GREEN: fallback and six parallel provider attempts preserve exact atomic
  counts.
- RED: route completion and first user token had no persisted timing
  semantics.
  GREEN: a fake clock proves first-write-wins route/TTFT behavior, agent-code
  filtering, and resume closure updates.
- RED: the explicit negated query entered model routing.
  GREEN: it deterministically selects account-data query with no router model
  call and no strategy artifact.
- RED: history could expose loosely shaped technical fields.
  GREEN: malicious intent/tool/attempt content is not serialized, while the
  public execution summary remains useful.
- RED: Playwright reused an unrelated existing Vite server, causing the API
  fixture and rendered DOM to disagree.
  GREEN: CI starts the exact worktree server and all four main-agent browser
  tests pass.

## Migration smoke

The online gate used a disposable `postgres:17-alpine` container, not the local
or production database:

1. upgraded a fresh schema through `20260730_0500`;
2. upgraded to `20260730_0600`;
3. verified Alembic current is `20260730_0600 (head)`;
4. downgraded to `20260730_0500`;
5. upgraded again to `20260730_0600`;
6. queried PostgreSQL metadata and confirmed all five columns are nullable,
   have no database default, and have nonnegative check constraints;
7. stopped the exact temporary container, which was created with `--rm`.

## Verification

- Backend V3 directed gate before final review:
  `111 passed` in 26.17 seconds.
- Backend directed gate after the final grouped retry-count query:
  `96 passed` in 18.49 seconds.
- Full backend after test-isolation fixes:
  `959 passed`, 5 warnings, in 294.47 seconds.
- Frontend unit suite:
  `70 files / 333 tests passed` in 23.69 seconds.
- Playwright `main-agent-v2.spec.ts`:
  `4 passed` in 8.4 seconds; the V3 ten-case matrix itself completed in
  2.8 seconds.
- Frontend lint:
  `0 errors`; 15 pre-existing React/Fast Refresh warnings.
- Frontend type check and production build:
  passed.
- Ruff lint over the full backend:
  passed.
- Ruff format over every Task 9 changed/new Python file:
  passed.
- `git diff --check`:
  passed.

## Full-suite failures found and resolved

The first full backend run completed with `957 passed / 2 failed`. Neither
failure was accepted or hidden:

- the cross-intent flow fixture still assumed synchronous execution after the
  API had moved to durable queued execution and could accidentally contact
  Redis from a developer `.env`;
- a private Skill fixture replaced `expert_codes` without replacing the new
  ordered `expert_stages`.

The fixtures now stub only the queue boundary, explicitly execute the persisted
Turn, and preserve the Skill definition invariant. Both focused regressions
passed before the second full run reached `959 passed`.

## Final review

### Correctness

- First-write-wins and resume-overwrite semantics match the timing contract.
- Provider-attempt counting is an atomic SQL update in the same commit as the
  corresponding `LLMCall`.
- Terminal replay exits before Gateway invocation.
- The ten capability cases preserve Turn/account ownership and do not dispatch
  the real-publish case before permission.

### Architecture

- Telemetry is isolated in one service and does not leak provider payloads into
  runtime context.
- The existing Task 8 single-Turn renderer remains the only chat renderer.
- No new dependency was added.

### Security

- API projections are explicit Pydantic allowlists.
- Prompt, raw provider body/error, raw tool input/output/meta, stack traces,
  keys, model parameters, and idempotency keys are not returned.
- Existing creator/account/thread authorization boundaries are unchanged and
  covered by the full suite.

### Performance

- Deterministic routes avoid the router model.
- Retry counts use one grouped database query rather than one query per tool.
- All Worker tests use bounded `asyncio.wait_for`; browser tests use
  `expect.poll` and no fixed sleeps.

### Verdict

Approve Task 9. No critical or required review finding remains.

## Known repository baseline

The full-repository command `ruff format --check .` reports 106 files that
predate Task 9 as unformatted under the locked Ruff 0.16.0. Reformatting those
unrelated files was intentionally not included in this focused task. Every
Python file changed or added by Task 9 passes the same formatter. The existing
repository-wide format CI step therefore remains a known baseline cleanup item;
lint, tests, migration smoke, frontend gates, and Task 9 formatting are green.

## Scope boundary

- No production deployment was performed.
- No production database or production Redis instance was contacted.
- Database downgrade remains an exceptional, approved data-loss operation;
  normal application rollback keeps the additive nullable columns.
