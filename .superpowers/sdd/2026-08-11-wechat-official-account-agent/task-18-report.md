# Task 18 Report - Product Metrics, Rollout, Security, and Smoke Gate

## Scope

- Added redacted WeChat API boundary logging in `backend/app/services/wechat_drafts.py`.
- Added product event persistence for article creation/versioning in `backend/app/services/wechat_articles.py`.
- Added product event persistence for capability checks in `backend/app/api/platform_integrations.py`.
- Added draft-sync requested/conflicted/completed/failed events in `backend/app/services/publishing.py`.
- Added rollout alert evaluator in `backend/app/services/wechat_rollout_alerts.py`.
- Added observability regression coverage in `backend/tests/test_wechat_observability.py`, including an independent commit-boundary regression for durable sync product events.
- Added rollout runbook in `docs/runbooks/wechat-official-account-rollout.md`.
- Added real-smoke approval guard in `frontend/e2e/wechat-article-flow.spec.ts`.
- Added shared SQLite UDF fixture registration in `backend/tests/conftest.py` to close an existing full-suite setup gap discovered during Task 18 verification.

## TDD Evidence

### RED -> GREEN: redacted WeChat boundary logging

- RED:
  - `cd backend`
  - `uv run pytest tests/test_wechat_observability.py -q`
  - Failed because `wechat_drafts` did not emit structured safe request logs.
- GREEN:
  - Same command after `WechatDraftClient._call_json_endpoint(...)` and `_log_wechat_api_request(...)`.
  - Latest full file result after Fix Round 1: `10 passed in 3.84s`.

### RED -> GREEN: article product events

- RED:
  - `test_create_article_records_safe_product_events`
  - `test_freeze_article_version_records_semantic_change_ratio`
  - Failed because no `wechat.article.*` events were persisted.
- GREEN:
  - Added `_record_article_event(...)` and persisted `wechat.article.created`, `wechat.article.initial_draft_ready`, `wechat.article.version_saved`.

### RED -> GREEN: capability checked event

- RED:
  - `test_capability_probe_records_safe_checked_event`
  - Failed because `GET /platform-integrations/wechat/{account_id}/capabilities` returned data without persisting a safe event.
- GREEN:
  - Added `wechat.capabilities.checked` with bounded payload.

### RED -> GREEN: draft-sync requested/conflicted/completed/failed events

- RED:
  - Focused tests in `backend/tests/test_wechat_observability.py` failed because publishing state changes had no product events.
- GREEN:
  - Added `_record_wechat_sync_product_event(...)` in `backend/app/services/publishing.py`.
  - Persisted:
    - `wechat.draft.sync_requested`
    - `wechat.draft.sync_conflicted`
    - `wechat.draft.sync_completed`
    - `wechat.draft.sync_failed`

### RED -> GREEN: shared SQLite test-environment repair

- Discovery command:
  - `cd backend`
  - `uv run pytest tests/test_wechat_observability.py tests/test_wechat_draft_sync.py tests/test_wechat_capabilities.py tests/test_wechat_authorization_api.py -q`
- RED:
  - 3 setup errors in `tests/test_wechat_authorization_api.py`
  - representative full-suite failures in:
    - `tests/test_account_data_models.py`
    - `tests/test_models.py`
    - `tests/test_turn_events_api.py`
  - Root cause: `sqlite3.OperationalError: no such function: wechat_article_document_is_valid`
  - Failure point: ad hoc SQLite engines hitting `Base.metadata.create_all(...)` without the shared check function.
- GREEN:
  - Added a shared test-only SQLite UDF registration hook in `backend/tests/conftest.py`.
  - Removed the now-redundant file-local workaround from `tests/test_wechat_authorization_api.py`.
  - No production authorization logic changed.

## Observability and Safety Contract

- Structured WeChat API log allowlist:
  - `event_name`
  - `endpoint`
  - `outcome`
  - `duration_ms`
  - `error_code`
  - `retryable`
  - `rid`
- Product event payloads are bounded and omit:
  - authorizer/component tokens
  - AppSecret values
  - article HTML/body
  - raw WeChat responses
  - file paths
  - free-form high-cardinality raw text
- Draft-sync product events are emitted only after the real state transition is recorded and do not alter sync semantics.

## Alert Evaluator

`backend/app/services/wechat_rollout_alerts.py` implements a pure-function evaluator for:

- component ticket older than 20 minutes
- repeated component refresh failure count >= 3
- repeated authorizer refresh failure count >= 3
- five-minute draft-sync failure rate > 5%
- any idempotency digest conflict count > 0
- any scope denial anomaly count > 0

This module does not introduce any telemetry vendor and is intended for runbook wiring.

## Runbook

Created: `docs/runbooks/wechat-official-account-rollout.md`

Includes:

- feature-flag / operational rollout guidance with production default effectively OFF
- callback URL, Token, EncodingAESKey checklist
- secret reference names without values
- manual capability-check procedure
- migration / worker restart ordering
- rollback without deleting tokens or draft mappings
- explicit prohibition on `freepublish_submit`
- real-smoke approval gate requiring explicitly named test organization and test account
- observability allowlist and evidence collection boundaries

## Official WeChat Documentation Status

As required, the following official URLs are listed as unverified because they were not reachable on 2026-08-12:

- `https://developers.weixin.qq.com/doc/oplatform/Third-party_Platforms/2.0/product/Mini_Programs/fast_registration_wxa/fastregistrationoverview.html`
- `https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html`
- `https://developers.weixin.qq.com/doc/offiaccount/Asset_Management/New_temporary_materials.html`
- `https://developers.weixin.qq.com/doc/offiaccount/Asset_Management/Adding_Permanent_Assets.html`
- `https://developers.weixin.qq.com/doc/offiaccount/Publish/Submit_Publish.html`

Runbook and rollout remain conservative:

- official docs current status: unverified
- `freepublish_submit`: prohibited
- real smoke: manual recheck required from an accessible network and WeChat admin console before any production enablement

## Frontend Validation

- Focused login page unit validation:
  - `cd frontend`
  - `npm.cmd test -- src/pages/Login.test.tsx`
  - Result: `1 file passed, 2 tests passed`
- Full frontend unit test gate:
  - `cd frontend`
  - `npm.cmd test`
  - Result: pass
- Frontend lint:
  - `cd frontend`
  - `npm.cmd run lint`
  - Result: pass
- Frontend build:
  - `cd frontend`
  - `npm.cmd run build`
  - Result: pass
- Main-agent bundle gate:
  - `cd frontend`
  - `npm.cmd run check:main-agent-bundle`
  - Result: `Main-agent bundle gate passed: initial=766362B, BrainHome=130284B, charts=lazy.`
- E2E:
  - `cd frontend`
  - `$env:PLAYWRIGHT_PORT='5189'; npm.cmd run test:e2e -- wechat-article-flow.spec.ts --workers=1`
  - Result: `1 skipped, 1 passed`
  - Meaning:
    - skipped test = intentional real-smoke guard with no approved org/account names present
    - passed test = mocked WeChat article flow

## E2E Root Cause Note

Earlier `/login` timeout was not a product behavior failure.

- Default Playwright config uses `reuseExistingServer: !process.env.CI`.
- Using the default port `5173` can reuse an unrelated local Vite server.
- Re-running on isolated `PLAYWRIGHT_PORT=5189` made the mocked flow pass in 10.6s.

## Backend Verification

Focused passing evidence:

- `cd backend`
- `uv run pytest tests/test_wechat_observability.py -q`
  - `9 passed in 2.99s`
- `uv run pytest tests/test_wechat_authorization_api.py -q`
  - `20 passed, 2 warnings in 10.37s`
- `uv run pytest tests/test_wechat_observability.py tests/test_wechat_draft_sync.py tests/test_wechat_capabilities.py tests/test_wechat_authorization_api.py -q`
  - `68 passed, 2 warnings in 27.49s`

Task-file targeted static checks:

- `uv run ruff check backend/app/services/wechat_drafts.py backend/app/services/wechat_articles.py backend/app/api/platform_integrations.py backend/app/services/publishing.py backend/app/services/wechat_rollout_alerts.py backend/tests/test_wechat_observability.py backend/tests/test_wechat_authorization_api.py`
  - pass
- `uv run ruff format --check ...same task Python files...`
  - pass

Branch-integration regression recovery:

- Added a shared SQLite `wechat_article_document_is_valid` registration hook in `backend/tests/conftest.py`
  - this removed the large `Base.metadata.create_all(...)` failure cluster caused by ad hoc SQLite engines outside the shared session fixture
- Removed the now-redundant local UDF registration from `backend/tests/test_wechat_authorization_api.py`
- Updated `tests/test_migrations.py::test_migration_head_is_wechat_article_evidence`
  - expected head changed from `20260811_0330` to current branch head `20260811_0400`
  - this was a stale assertion after the later migration landed
- Preserved the Task 18 semantic-ratio behavior while avoiding an unrelated regression in `tests/test_wechat_article_api.py`
  - kept the version-conflict test semantics intact by moving the extra previous-version fetch off the `session.scalar(...)` path used by that test's call-count probe

## Full-Repo Gate Status

These gates were executed because the brief requires them, but they are not yet globally green in the current repository state:

- `cd backend && uv run ruff check app tests`
  - fail
  - Observed failures include unrelated existing files such as:
    - `app/api/conversations.py`
    - `app/api/wechat_articles.py`
    - `tests/test_wechat_article_api.py`
  - Task 18 touched-file subset is green.
- `cd backend && uv run ruff format --check app tests`
  - fail
  - Repository-wide output reports many pre-existing unformatted files beyond Task 18 scope.
- `cd backend && uv run mypy app`
  - fail with 86 errors in 17 files
  - Observed failures are in unrelated existing modules such as:
    - `app/llm/adapters/deterministic_test.py`
    - `app/services/data_import/service.py`
    - `app/orchestrator/skill_runtime.py`
    - `app/services/runtime_state.py`
    - `app/api/turn_events.py`
- `cd backend && uv run pytest -q`
  - final rerun result after branch-integration fixes:
    - `2010 passed, 21 skipped, 7 warnings in 668.53s (0:11:08)`

## Full Pytest Triage Against Clean `25d1747`

To distinguish Task 18 regressions from existing repository failures, a detached baseline worktree was created at:

- `C:\Users\AKSSINA\Desktop\Workplace\GEOzx\.worktrees\wechat-official-agent-baseline`
- revision: `25d1747898ba2d8b94dc13ee84394085ea220ae0`

Using the current backend virtualenv but baseline source tree, the following representative failures reproduced unchanged on clean `25d1747`:

- `tests/test_account_data_models.py::test_metric_snapshot_requires_account_when_source_links_are_set[import_batch_id-<lambda>]`
- `tests/test_models.py::test_org_user_relationship`
- `tests/test_turn_events_api.py::test_stream_poll_fallback_discovers_commit_without_redis_message`

All three fail during `Base.metadata.create_all(...)` with the same root cause:

- `sqlite3.OperationalError: no such function: wechat_article_document_is_valid`

This shows the large SQLite failure cluster is a pre-existing baseline issue rather than a Task 18 regression.

The migration-head assertion also reproduces unchanged on clean `25d1747`:

- `tests/test_migrations.py::test_migration_head_is_wechat_article_evidence`
- actual head: `20260811_0400`
- expected by test: `20260811_0330`

This is also baseline, not introduced by Task 18.

Additional verification:

- `tests/test_wechat_article_images.py -q`
  - current worktree: pass
  - baseline worktree: pass
  - implication: the stale `lastfailed` cache entry using the older name `...sanitized_error_category` does not represent a current failure
- `tests/_review_tmp_task14_probe.py`
  - file does not exist in either current or baseline worktree
  - implication: this is also a stale `lastfailed` cache key, not an actionable current regression

The one improved current/baseline difference is intentional and local:

- `tests/test_wechat_authorization_api.py`
  - baseline `25d1747`: `17 passed, 3 errors`
  - current Task 18 worktree: `20 passed, 2 warnings`
  - initial reason: minimal isolated SQLite UDF registration added only in that test file to unblock required Task 18 verification
  - final branch fix: shared SQLite registration moved into `tests/conftest.py`, and the file-local duplication was removed

After the shared fixture fix and stale migration-head assertion update, the previously failing full-suite clusters closed:

- representative recovery file set:
  - `tests/test_account_data_models.py`
  - `tests/test_ai_coo_models.py`
  - `tests/test_data_import_preview.py`
  - `tests/test_identity_governance_models.py`
  - `tests/test_model_provider_models.py`
  - `tests/test_models.py`
  - `tests/test_turn_events_api.py`
  - `tests/test_turn_provenance.py`
  - `tests/test_user_deletion_api.py`
  - `tests/test_wechat_article_api.py`
  - `tests/test_wechat_article_images.py`
  - `tests/test_wechat_authorization_api.py`
  - `tests/test_migrations.py`
- command result:
  - `201 passed, 6 skipped, 3 warnings in 88.94s`

## Diff / Secret Checks

- `git diff --check`
  - no diff-format errors
- changed-file static checks:
  - `uv run ruff check tests/conftest.py tests/test_wechat_authorization_api.py tests/test_migrations.py app/services/wechat_articles.py app/services/wechat_drafts.py app/api/platform_integrations.py app/services/publishing.py app/services/wechat_rollout_alerts.py tests/test_wechat_observability.py`
    - pass
  - `uv run ruff format --check tests/conftest.py tests/test_wechat_authorization_api.py app/services/wechat_articles.py app/services/wechat_drafts.py app/api/platform_integrations.py app/services/publishing.py app/services/wechat_rollout_alerts.py tests/test_wechat_observability.py`
    - pass
  - `uv run ruff format --check tests/test_migrations.py`
    - fail on broad pre-existing whole-file formatting debt unrelated to the one-line head assertion update
- Searched changed files for token/secret leakage patterns
  - only expected code references and dummy test strings were found
  - no real secret values were introduced

## Files Outside Brief and Reasons

- `backend/app/services/publishing.py`
  - approved minimal extension for sync product events on real draft-sync state transitions
- `backend/app/services/wechat_rollout_alerts.py`
  - approved pure-function alert evaluator extraction
- `backend/tests/conftest.py`
  - shared SQLite UDF registration to repair branch-wide test-environment parity for all SQLite-backed test engines
- `backend/tests/test_migrations.py`
  - stale migration-head assertion updated from `20260811_0330` to current branch head `20260811_0400`

## Real Smoke Status

- No real WeChat account actions were executed.
- Real smoke remains blocked pending explicit operator approval of:
  - one named test organization
  - one named WeChat official account

## Residual Concerns

- Backend global static/type gates are already red outside Task 18 scope, so repository-wide green status is not yet available.
- Based on clean-worktree comparison, the original dominant full-`pytest` failures were baseline SQLite test-environment gaps and a stale migration-head assertion; both are now repaired in the current branch.
- `tests/test_migrations.py` still fails `ruff format --check` as a whole file because of pre-existing formatting debt outside the one-line assertion update; behavior and `ruff check` are green.

## Fix Round 1

Review-driven corrections landed after commit `53e250d`:

- Added the exact required authorization lifecycle product events at authoritative boundaries:
  - `wechat.authorization.started`
  - `wechat.authorization.completed`
  - `wechat.authorization.failed`
  - `wechat.authorization.revoked`
- Preserved the older audit/raw lifecycle events (`wechat.authorization.session.created`, `wechat.authorization.session.consumed`, `wechat.unauthorized`, `wechat.{info_type}`) for compatibility.
- Added the exact required image product events:
  - `wechat.images.generate_all_requested`
  - `wechat.images.image_selected`
- Added bounded key-interaction metric events with a single stable contract:
  - event name: `wechat.article.key_interaction_recorded`
  - bounded payload:
    - `account_id`
    - `article_id`
    - `interaction_type`
    - `count`
- Current bounded `interaction_type` enum:
  - `images_generate_all_requested`
  - `image_selected`
  - `version_saved`
  - `draft_sync_requested`
- Definition:
  - this metric records one durable count per real user-visible action boundary
  - it does not record AI-autogenerated initial draft creation
  - it stores no prompt text, HTML, token, path, or free-form user input

Draft-sync durability correction:

- Added a single safe helper in `backend/app/services/publishing.py` that persists product events with an independent short-lived session bound to the same engine:
  - used for `wechat.draft.sync_conflicted`
  - used for `wechat.draft.sync_failed`
  - used for `wechat.draft.sync_completed`
- This removes the prior dependency on `_append_wechat_sync_step(...)` commits for non-lineaged manual articles.
- It also prevents the helper from flushing unrelated dirty ORM objects from the caller session.
- Manual rollback probes are now covered by tests that intentionally call `session.rollback()` after the raised conflict/failure and verify the product event from a fresh session.

Fix Round 1 TDD evidence:

- Authorization exact events RED -> GREEN:
  - `cd backend`
  - `uv run pytest tests/test_wechat_authorization_api.py -k "authorization_session_returns_official_url_and_hashes_state or authorization_state_is_consumed_once_and_credentials_are_encrypted or authorization_exchange_failure_records_failed_product_event or revocation_committed_during_code_exchange_wins_across_sessions" -q`
  - RED: `3 failed, 1 passed`
  - GREEN: `4 passed`
- Image exact events and bounded interaction RED -> GREEN:
  - `uv run pytest tests/test_wechat_article_images.py -k "batch_request_is_committed_before_provider_and_replay_returns_persisted_result or selecting_replacement_keeps_old_asset_and_rejects_cross_article_asset or upload_reencodes_without_exif_and_selects_ready_user_asset" -q`
  - RED: `3 failed`
  - GREEN: `3 passed`
- Draft-sync durability and interaction RED -> GREEN:
  - `uv run pytest tests/test_wechat_observability.py -k "create_article_records_safe_product_events or freeze_article_version_records_semantic_change_ratio or draft_sync_records_requested_and_completed_events or draft_sync_records_conflicted_event or draft_sync_records_failed_event" -q`
  - RED: `5 failed`
  - GREEN: `5 passed`
- Independent commit-boundary RED -> GREEN:
  - `uv run pytest tests/test_wechat_observability.py -k "sync_product_event_helper_uses_independent_commit_boundary" -q`
  - RED: `1 failed`
  - GREEN: `1 passed`

Adjacent regression coverage after the fix:

- `uv run pytest tests/test_wechat_authorization_api.py -q`
  - `21 passed, 2 warnings in 12.01s`
- `uv run pytest tests/test_wechat_article_images.py -q`
  - `21 passed in 10.38s`
- `uv run pytest tests/test_wechat_observability.py -q`
  - `10 passed in 3.84s`
- `uv run pytest tests/test_wechat_draft_sync.py -q`
  - `31 passed in 14.54s`
