# Main Agent V2 final fix report

## STATUS

**FIXES COMPLETE — RELEASE GATES REMAIN BLOCKED BY THE DOCUMENTED REPOSITORY BASELINE.**

All three final-review findings are fixed with regression coverage. The final
independent review found no remaining P0-P2 issue in this fix wave. The branch
is not represented as fully release-green because the complete backend suite
still has the same nine pre-existing failures and repository-wide Mypy still
has the same 227 pre-existing errors recorded before this wave.

Implementation commit:

- `abe69b0` — `fix: harden main agent v2 recovery invariants`

This report is committed separately after its evidence is finalized; its commit
hash is supplied in the final handoff.

## Finding 1 — stranded running SkillRun

### RED

- A stale `running` `SkillRun` was returned as if it were a reusable terminal
  result.
- Worker recovery fell into the legacy runtime and failed while validating a
  missing legacy `IntentDecision`.
- A stale SQLAlchemy identity-map could let a former owner heartbeat after a
  different session had taken over the expired lease.
- Ambiguous-side-effect recovery cleared the lease before the outer Turn
  delivery made the `AgentRun` terminal, leaving a crash window.
- Replaying an active owner through the Turn service cleared its lease and set
  `finished_at`.
- Recovery of a persisted SkillRun could be broken by later publication,
  role, platform, or route-registry changes.

The focused RED assertions observed the incorrect states directly:

- stale execution returned `running` instead of a typed failure;
- stale owner heartbeat returned `True` after takeover;
- atomic-recovery test still observed `AgentRun.status == "running"`;
- unpublished recovery returned `None` through the task-free failure path;
- duplicate Turn retry changed `phase` from `skill_runtime` to `running`.

### GREEN

- `AgentRun` is now the lease authority for Skill execution. A new SkillRun
  records an owner, expiry, attempt, start time, and heartbeat without requiring
  a migration because the durable lease columns already existed.
- Every tool, expert, Critic iteration, and final Artifact boundary heartbeats
  before and after the potentially long operation.
- `heartbeat_agent_run` reloads and row-locks the database record with
  `populate_existing=True`; a stale session cannot reclaim a lease from the
  current owner.
- An active owner returns a read-only `running` projection. Duplicate Turn
  retries do not execute tools, clear the lease, change the phase, or write a
  terminal timestamp.
- An expired owner can be taken over. Completed tool calls, expert invocations,
  and Critic ledgers retain their existing idempotency keys.
- If recovery finds a `planned`/`running` tool call or a `queued`/`running`
  expert invocation, the external outcome is ambiguous. The runtime does not
  replay it. In one commit it fails the child rows, `BrainTask`, `SkillRun`,
  `ConversationTurn`, and `AgentRun` with
  `SKILL_EXECUTION_INTERRUPTED`, clears the lease, and writes a valid persisted
  `TurnExecutionResult`.
- The worker can close either a still-running SkillRun or a SkillRun that
  became terminal immediately before the original request process crashed.
- Worker recovery reconstructs the Turn only after org/thread/turn/run/skill
  provenance checks. It resumes from the persisted SkillRun route and does not
  re-apply the current public catalog, role, platform, or route-registry policy.
  Normal new requests still use the canonical full validation path.

No schema migration was needed.

## Finding 2 — stale Artifact acceptance

### RED

After V2 had been selected and V3 existed, accepting V2 returned HTTP 200 and
superseded the newer draft.

### GREEN

- Revision creation and acceptance now share one latest-version guard.
- The guard locks the canonical `ContentItem` row before reading the maximum
  `(content_item_id, deliverable_type)` version, serializing revision and
  acceptance mutations in PostgreSQL.
- Accepting the current version remains idempotent.
- Accepting any superseded or non-latest version returns HTTP 409 with:

  - `code: ARTIFACT_VERSION_CONFLICT`
  - selected and latest version details
  - no mutation to the selected or latest Artifact

- Acceptance only supersedes lower versions; it can never supersede a later
  draft defensively.

Coverage includes both an idempotent repeat followed by a newer version and a
first acceptance attempt that is already stale.

## Finding 3 — explicit Skill validation bypass

### RED

Unknown, platform-incompatible, and unpublished explicit Skill codes entered
the formal Skill path. They could persist a SkillRun or reach the executor
instead of being rejected by the canonical capability contract.

### GREEN

- Public Skill policy moved to a shared server-owned catalog used by both the
  catalog API and Turn routing.
- A new explicit request must pass:

  1. registry lookup;
  2. account context;
  3. platform compatibility;
  4. composer publication;
  5. enabled-state and role compatibility.

- Invalid explicit requests are blocked task-free with typed codes such as
  `UNKNOWN_SKILL`, `UNSUPPORTED_PLATFORM`, and `UNPUBLISHED_SKILL`.
- The blocked path creates no `BrainTask`, `SkillRun`, `Deliverable`, or
  `ContentItem`, and an incompatible request never reaches the Skill executor.
- Persisted recovery is intentionally separate from admission policy and is
  protected by durable provenance checks as described above.

## Verification evidence

### Focused and related backend coverage

- Lease/recovery review regressions:
  - initial RED: 3 focused failures covering stale heartbeat, non-atomic
    termination, and changed-policy recovery;
  - GREEN: `5 passed`.
- Active-owner and frozen recovery routing:
  - initial RED: `2 failed`;
  - GREEN: `2 passed`.
- Explicit invalid-Skill coverage:
  - initial RED: `4 failed`;
  - GREEN: `4 passed`.
- Artifact stale-acceptance coverage:
  - initial RED: stale acceptance returned 200;
  - GREEN: `2 passed`.
- Related suite excluding the two documented `test_agent_runs.py` baseline
  failures:
  - `119 passed, 2 deselected, 68 warnings`.
- Latest-state Skill/worker rerun after the final no-behavior type refinement:
  - `22 passed`.

### Complete backend gates

- `uv run ruff check .`
  - PASS: `All checks passed!`
- `uv run mypy app`
  - BASELINE FAIL: `Found 227 errors in 40 files (checked 189 source files)`.
  - This matches the pre-wave ledger count. The source-file count increased by
    one because this wave added `public_catalog.py`.
- Scoped Mypy over the seven touched production modules:
  - BASELINE FAIL: 15 pre-existing errors in `artifacts.py`,
    `skill_runtime.py`, and `turn_execution.py`.
  - The three type errors introduced during implementation were corrected
    before final verification; `agent_runs.py`, `worker.py`, `api/skills.py`,
    and `public_catalog.py` add no reported error.
- `uv run pytest`
  - BASELINE FAIL: `9 failed, 818 passed, 559 warnings in 277.72s`.
  - All 13 tests added or strengthened by this wave passed.
  - The nine failures exactly match the pre-wave ledger:
    - `tests/test_agent_runs.py::test_worker_executes_queued_agent_run_and_persists_completion`
    - `tests/test_agent_runs.py::test_worker_does_not_retry_invalid_model_route_configuration`
    - `tests/test_agents_api.py::test_direct_agent_run_creates_real_artifact_and_pending_acceptance`
    - `tests/test_agents_api.py::test_direct_agent_handoff_returns_audited_main_agent_draft`
    - `tests/test_brain_api.py::test_brain_message_replans_after_each_expert_result`
    - `tests/test_brain_api.py::test_brain_runtime_cannot_replace_required_expert_with_direct_response`
    - `tests/test_brain_api.py::test_brain_runtime_recovers_invalid_controller_decision_with_dynamic_plan`
    - `tests/test_brain_api.py::test_smart_runtime_resumes_from_permission_with_decision_in_parent_context`
    - `tests/test_review_workspace_api.py::test_review_workspace_uses_account_level_imports_when_content_attribution_is_missing`

### Frontend and browser gates

- Literal `pnpm test`
  - BLOCKED by the local PowerShell execution policy loading `pnpm.ps1`.
- Windows-equivalent `pnpm.cmd test`
  - PASS: `68` files and `362` tests.
- `pnpm.cmd lint`
  - PASS: `0 errors`; 15 documented existing warnings.
- `pnpm.cmd build`
  - PASS: TypeScript check and Vite production build; 3915 modules transformed.
  - Existing chunk-size warnings remain.
- `pnpm.cmd exec playwright test main-agent-v2.spec.ts --reporter=line`
  - PASS: `1 passed`.

### Diff and review gates

- `git diff --cached --check`
  - PASS before the implementation commit.
- Staged credential-pattern scan
  - PASS: no credential-like secret found.
- Independent final review
  - Initial review found two P1 lease/crash issues and one P2 recovery-policy
    issue; each received a dedicated RED/GREEN fix.
  - Final rereview: no remaining P0-P2.

## Self-review

- Correctness: database-backed lease fencing, atomic terminal recovery, and
  latest-Artifact locking cover the failure modes rather than only changing
  response copy.
- Idempotency: SkillRun, tool, expert, Critic, Artifact version, and Turn
  ownership keys remain durable; active duplicate requests are read-only.
- Safety: ambiguous external outcomes are never replayed, typed recovery copy
  contains no provider details, and invalid explicit requests cannot create
  formal records.
- Scope: no unrelated production behavior was changed. Existing user work in
  the cost files and handoff document was preserved and excluded from staging.
- Review: the final independent reviewer reported no remaining P0-P2.

## Remaining release blockers

The fix wave itself has no known P0-P2. Repository release readiness remains
blocked only by the documented nine backend test failures and 227 Mypy errors
listed above; resolving that baseline is outside this final finding set.
