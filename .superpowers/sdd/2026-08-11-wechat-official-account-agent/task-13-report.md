# Task 13 Report: Durable WeChat Draft Synchronization

## Scope and result

- Added an account-scoped POST approval boundary and a safe GET ledger endpoint.
- Extended the existing publish-job ledger without changing Douyin defaults or output.
- Frozen exact immutable article/material facts, canonical request digests, approval snapshots,
  capability snapshots, safe progress, external intent/result events, and remote mappings.
- Implemented add/update/create-new conflict policies, partial image resume, fail-closed
  reconciliation, retry classification, and optional WorkTurn progress.
- The real provider factories intentionally remain disabled (structured 503) until Task 18
  supplies deployment wiring and live capability/token/client configuration.

## TDD evidence

Every production behavior was driven by a focused failure first. The important REDs were:

1. Missing model enum/columns and request/output schemas failed collection/import.
2. Missing prepare/execute functions failed before any provider behavior existed.
3. Same-key/different-digest, unresolved intent, body-image resume, concurrent attempt claim,
   and the complete conflict matrix each failed at the previously unsafe branch.
4. Missing HTTP factories/routes returned attribute errors/404 before safe API wiring.
5. Approval tampering, revoked authorization, missing capability, 429-without-hint, and cover
   failure produced six focused failures before fail-closed handling.
6. Provider success followed by result-commit failure initially had no demonstrated recovery;
   the final test proves one provider call, an intent without a result, and mandatory manual
   reconciliation on re-entry.
7. WorkTurn success initially emitted zero events; subsequent REDs exposed the wrong scope
   class and payload allowlist. Final coverage proves idempotent public started/completed and
   safe failed events, while manual API articles fabricate no lineage.
8. Readiness failure before job creation initially had no progress event. The final path uses
   exact deliverable lineage and a hashed event key, creates no job, and performs no write.
9. The migration test first failed because revision `20260811_0400` did not exist.

Final Task 13 result: `28 passed`.

## External-call and recovery proof

- Same approved new-draft replay: body uploads `2`, cover uploads `1`, draft adds `1`.
- Retry after the second body upload fails: completed first URL is retained; total body calls
  are `3`, not `4`; cover/add each occur once.
- Existing mapping policies: `fail` and `overwrite_confirmed` read once and update once only
  after the required fresh hash comparison; stale confirmation updates zero times.
- `create_new` performs zero get/update calls and replaces the mapping only after add succeeds.
- A second worker cannot join the same `(status, retry_count)` attempt.
- An intent without a result, including simulated provider-success/result-DB-failure, enters
  `wechat_reconciliation_required` and never automatically repeats the write.

## Transaction review

- Job creation commits before capability probe; capability state commits before token access.
- No database row lock or caller transaction spans capability, token, upload, get, add, or
  update calls.
- Each provider write is preceded by a committed scoped intent and followed by a committed
  allowlisted success/failure result.
- Completed body URLs and cover media IDs commit before the next side effect.
- Conditional claim checks status and attempt; stale workers cannot increment or overwrite a
  terminal/newer state.
- Mapping/media/hash/exact deliverable and successful job result commit atomically after the
  remote add/update succeeds. Ambiguous database failure does not claim success.

## STRIDE and five-axis self-review

- **Spoofing / elevation:** org plus account access is checked for prepare, execute, and GET;
  only LEAD/OPERATOR (or org admin) can approve. Cross-scope access is 404.
- **Tampering:** approval fields are fully matched to the frozen job; files are resolved from
  scoped material rows, bounded, re-hashed, and checked under the storage root.
- **Repudiation:** immutable approval, digest, attempt, intent/result events, and public turn
  steps provide an auditable ledger.
- **Information disclosure:** API output is explicit and safe. Tokens, HTML, file paths,
  bytes, prompts, raw provider responses, and raw capability responses are absent from
  job/event/API payloads. Observed remote hash is deliberately exposed for conflict UX.
- **Denial/retry:** replay is idempotent; retry is limited to transport/5xx and rate limits
  carrying an explicit retry hint. Business, permission, malformed, and unhinted 429 failures
  are terminal.
- **Correctness:** exact immutable version and selected scoped assets are used; no working-copy
  substitution occurs; conflict policies match the frozen mapping and fresh remote hash.
- **Compatibility:** fresh Douyin service/API regressions pass; legacy job defaults and rows
  survive the PostgreSQL migration round trip.
- **Maintainability:** the implementation is deliberately confined to the Task 13 owned
  service. It is large; a later no-behavior-change extraction into a dedicated WeChat sync
  module is recommended after the SDD integration window.

## PostgreSQL 16 migration evidence

Temporary Docker PostgreSQL 16 database: `wechat_task13` on local port `55443`.

1. Migrated a fresh database to `20260811_0330`.
2. Seeded a legacy Douyin publish job.
3. Upgraded to `20260811_0400`; verified legacy status `draft`, operation default/backfill
   `legacy_douyin_publish`, all six columns, article-version index, and `ON DELETE RESTRICT` FK.
4. Seeded an active WeChat sync row and proved downgrade fails closed.
5. Removed the active row, downgraded to `0330`, proved the legacy row remained, then upgraded
   to `0400` again and reverified defaults/index/FK.

PostgreSQL status enum labels are additive and intentionally remain after downgrade; the
downgrade never silently coerces active WeChat rows.

## Final verification

```text
pytest tests/test_wechat_draft_sync.py -q
28 passed

pytest tests/test_wechat_draft_sync.py tests/test_content_publishing_skill.py tests/test_wechat_draft_client.py -q
73 passed

pytest tests/test_publishing_service.py tests/test_publishing_api.py tests/test_wechat_renderer.py tests/test_wechat_article_images.py -q
73 passed

ruff check <Task 13 Python files>
All checks passed

ruff format --check <Task 13 Python files>
5 files already formatted

mypy app/models/publishing.py app/schemas/publishing.py app/services/publishing.py app/api/wechat_articles.py
Success: no issues found in 4 source files

python -m py_compile migrations/versions/20260811_0400_wechat_draft_sync_jobs.py
git diff --check
passed
```

Credential-pattern scan found no credential literals. Intent/result and API assertions also
prove that access/refresh tokens, raw HTML, prompts, local paths, and raw provider responses
are not serialized.

## Known limitations

- Task 18 must inject real capability/token/draft-client factories and perform official live
  endpoint contract verification before production synchronization is enabled.
- PostgreSQL cannot remove additive enum labels transactionally; downgrade behavior is
  explicitly documented and fail-closed.
- WorkTurn progress is emitted only when the exact immutable deliverable has complete
  thread/turn lineage. Manual API-created articles correctly rely only on the durable job.

Commit: this report is part of the atomic Task 13 feature commit; the final SHA is reported
to the controller after commit creation.

## Review fix round 1

Addressed both findings from `task-13-review.md` with focused RED/GREEN coverage:

1. A job left `wechat_running` after its current-attempt failure result was committed no
   longer wedges as `WECHAT_DRAFT_SYNC_ALREADY_RUNNING`. Recovery verifies scoped job,
   attempt, operation/intent matching, and allowlisted result shape, then moves a failed or
   malformed result to `wechat_reconciliation_required`. The regression simulates one
   provider call and proves both recovery and replay make zero additional external calls.
   A normal second worker with no result remains `WECHAT_DRAFT_SYNC_ALREADY_RUNNING`.
2. The POST approval boundary now narrowly maps a visible REVIEWER role mismatch from 403 to
   fail-closed 404. GET behavior and the shared workspace helper are unchanged. The regression
   proves no publish job is created.

Round 1 verification after the fixes:

```text
pytest tests/test_wechat_draft_sync.py -q
31 passed

pytest tests/test_wechat_draft_sync.py tests/test_content_publishing_skill.py tests/test_wechat_draft_client.py -q
76 passed

pytest tests/test_publishing_service.py tests/test_publishing_api.py tests/test_wechat_renderer.py tests/test_wechat_article_images.py -q
73 passed

ruff / format / mypy / migration py_compile / diff check
passed
```
