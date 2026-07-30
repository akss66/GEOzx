# Task 5 Report — Runtime Scope and Lineage Constraints

## Outcome

Operations Brain V3 runtime writes now use one immutable `RuntimeScope` containing
organization, user, account, conversation thread, turn, run, task, and optional
SkillRun identity. The scope is validated against persisted ownership and account
relationships before Harness, Tool, or formal Deliverable writes.

Legacy records remain compatible only when all provenance fields are null. Partial
legacy provenance is rejected.

## TDD evidence

The following behaviors were introduced with failing tests first, then implemented
until green:

- Missing `RuntimeScope` module and immutable full-graph validation.
- Cross-user, cross-organization, cross-account, cross-thread, cross-turn,
  cross-run, cross-task, and cross-SkillRun write rejection.
- Formal Deliverable write boundary rejecting partial legacy provenance and
  content/account mismatches.
- Harness rejecting a mismatched scope before creating an `AgentInvocation`.
- Tool execution requiring V3 scope and idempotency reuse comparing both
  `invocation_id` and the complete persisted scope.
- Composite provenance foreign keys rejecting crossed source graphs.
- Migration preflight accepting only deterministically repairable partial rows,
  backfilling only from explicit canonical SkillRun/Invocation sources, retaining
  all-null legacy rows, and stopping on conflicts.
- Permanent conversation deletion retaining formal `BrainTask`, `ContentItem`, and
  `Deliverable` records while clearing Deliverable provenance and deleting
  conversation-bound run, SkillRun, invocation, and tool logs.
- Artifact revisions returning HTTP 409 for corrupt lineage while unauthorized
  artifact reads remain hidden as 404.

## Implementation

- Added immutable `RuntimeScope` and full persisted graph validation.
- Added `write_runtime_deliverable` as the audited formal-output boundary.
- Routed Skill Runtime, Agent Harness, and Tool Executor through the canonical
  scope.
- Added full-scope and invocation-aware tool idempotency checks.
- Added model-level composite unique constraints and provenance foreign keys.
- Added migration `20260730_0300_runtime_scope_constraints` with two-phase
  preflight/backfill/preflight behavior and reversible constraints.
- Updated conversation deletion and artifact revision lineage handling.

## Verification

- Directed regression suite: **95 passed**, one unrelated Alembic configuration
  deprecation warning.
- Focused RuntimeScope/Harness/Tool suite: **15 passed**.
- Ruff on every changed Python file: **passed**.
- `git diff --check`: **passed**.
- `alembic heads`: **20260730_0300 (head)**.
- The new migration's PostgreSQL DDL operations compile successfully in isolation.

The complete backend test suite was attempted twice but exceeded the available
command windows (120 seconds and 300 seconds) without reporting a failure. No
stray test processes remained. The 95-test directed suite covers every changed
runtime, model, migration, artifact, deletion, and account-inspection path.

## Known repository concern

Full-chain `alembic upgrade head --sql` is blocked by pre-existing migration
`20260716_0200_client_workspace_shell.py`, which calls `scalar_one()` while running
in Alembic offline mode. This failure predates Task 5 and occurs before the new
`20260730_0300` migration is reached. The Task 5 migration itself compiles for the
PostgreSQL dialect and is the single Alembic head.
