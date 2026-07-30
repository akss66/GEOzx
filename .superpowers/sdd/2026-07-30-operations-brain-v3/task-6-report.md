# Task 6 implementation report

## Delivered

- Added stable `SkillDefinition.expert_stages` and `critic_policy` contracts. Stage
  flattening must exactly match `expert_codes`; catalog projection exposes both.
- Added frozen `SkillRun.input_hash` (canonical JSON + SHA-256 including account
  scope), exact-version recovery, logical `skill:{code}` idempotency for new runs,
  legacy versioned-key recovery, ambiguity detection, and non-retryable integrity /
  version / concurrent-winner conflicts.
- Worker no longer selects the newest SkillRun by id. Multiple active candidates are
  rejected as `SKILL_RECOVERY_AMBIGUOUS`.
- Trace-only specialist output is authoritative in
  `AgentInvocation.upstream[].trace_only_output`; `AgentRun.result_payload` remains
  a compatibility fallback.
- Formal Skill deliverables now validate their output model, require a completed
  in-scope expert Invocation, record that producer id, and use the producer expert
  code rather than `00-decision`.
- Added migration `20260730_0400_skill_recovery_freeze`, including ambiguous-active
  preflight, canonical hash backfill, 64-character validation, non-null convergence,
  and downgrade.

## TDD evidence

- RED: `skill_input_hash` import missing; new registry stage/catalog tests failed.
- GREEN: registry/model/turn suite passed: `68 passed`.
- RED: frozen recovery conflict APIs missing.
- GREEN: exact version, stable hash, tamper and missing-version tests passed.
- RED: trace-only Invocation had no authoritative persisted output.
- GREEN: trace-only replay reads Invocation first and retains legacy fallback.

## Verification

- Directed Task 6 suite:
  `108 passed, 1 warning in 22.43s`.
- `uv run ruff check app tests migrations/versions/20260730_0400_skill_recovery_freeze.py`
  passed.
- `uv run alembic heads` -> `20260730_0400 (head)`.
- `git diff --check` passed.

## Concerns / follow-up

- `expert_stages` is now a validated execution contract and formal producer/trace
  boundaries are enforced, but the current runtime still executes experts
  sequentially on the request session. The independent-`AsyncSession`, bounded
  same-stage parallel executor requested by the brief remains to be completed in
  the review/fix loop; enabling `asyncio.gather` on the current shared session would
  be unsafe and was intentionally not done.
- The migration rejects malformed JSON and ambiguous active runs transactionally.
  Exact Registry-version availability is enforced at recovery time rather than
  hard-coded into the migration, so deployments can retain organization-specific
  historical definitions without migration-time false positives.
