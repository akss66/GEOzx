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
- Replaced the private route-helper matrix with ten API-to-Worker integration
  scenarios. Each scenario now submits a real Turn, executes the persisted Run,
  polls the public Turn API, and checks the resulting Task, SkillRun,
  invocation, tool, artifact, and account provenance.
- Hardened every public conversation projection behind one discriminated,
  typed Pydantic allowlist. Raw answer, approval, execution-summary, unknown,
  provider, prompt, tool payload, and trace fields from `AgentRun.result_payload`
  fail closed; approvals and execution summaries are rebuilt from authoritative
  database rows.
- Added a deterministic provider and confirmation tool that exist only when
  both `ENVIRONMENT=test` and the explicit deterministic-provider flag are
  enabled. The real service smoke therefore exercises API, Redis, ARQ,
  LangGraph, DurableToolExecutor, approval persistence, and Turn projection
  restoration without an outbound fake-model HTTP bypass.
- Bound main-graph V3 ToolCalls to an immutable RuntimeScope. Operation Tasks
  now create account-scoped ContentItems, and persisted ToolCalls retain their
  Thread and Turn ownership so approval controls survive history reload.
- Replaced the unreliable repository-wide Ruff format baseline with a
  changed-Python gate that combines committed, staged, unstaged, and untracked
  files and fails on the exact changed set.
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

- Final full backend release gate:
  `970 passed`, 5 warnings, in 297.61 seconds.
- Review-fix affected backend gate:
  `155 passed`, 1 warning, in 66.97 seconds.
- Projection, Gateway, tool, Turn-execution, V3 integration, performance, and
  changed-format directed gate:
  `129 passed`, 4 warnings, in 35.67 seconds.
- Frontend unit suite:
  `70 files / 333 tests passed` in 24.35 seconds.
- Playwright `main-agent-v2.spec.ts`:
  `4 passed` in 8.5 seconds; the V3 ten-case UI contract itself completed in
  2.8 seconds.
- Real backend + Worker Playwright:
  greeting, account-data query, and persisted approval restoration
  `1 passed` in 5.2 seconds.
- Frontend lint:
  `0 errors`; 15 pre-existing React/Fast Refresh warnings.
- Frontend type check and production build:
  passed.
- Ruff lint over the full backend:
  passed.
- Changed-Python Ruff format gate:
  all 28 changed/new Python files passed.
- `git diff --check`:
  passed.

## Full-suite failures found and resolved

The final-review full backend run completed with `967 passed / 3 failed`.
Neither failure was accepted or hidden:

- two legacy `/brain/messages` tests used AgentRuns that correctly had neither
  conversation Thread nor Turn ownership. The V3 scope helper now preserves
  that legacy path while still rejecting partial conversation provenance;
- the ambiguous external-write fixture had conversation provenance but omitted
  the account-scoped ContentItem required by RuntimeScope, so the fixture was
  corrected instead of weakening the production invariant.

The three focused regressions passed, the affected six-file gate reached
`155 passed`, and the second full run reached `970 passed`.

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

## Format gate scope

The repository contains pre-existing files that do not match the locked Ruff
formatter. CI now computes the exact changed Python set from the PR base,
staged, unstaged, and untracked files, filters deleted paths, and runs the
locked formatter only on that set. Repository-wide Ruff lint remains enabled.

## Scope boundary

- No production deployment was performed.
- No production database or production Redis instance was contacted.
- Database downgrade remains an exceptional, approved data-loss operation;
  normal application rollback keeps the additive nullable columns.
