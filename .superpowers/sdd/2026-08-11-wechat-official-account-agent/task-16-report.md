# Task 16 Report

Date: 2026-08-12
Worktree: `C:\Users\AKSSINA\Desktop\Workplace\GEOzx\.worktrees\wechat-official-agent`
Task: 微信公众号文章工作台

## Scope delivered

- Added WeChat article workspace route at `wechat-articles/:articleId`.
- Added frontend service layer for working copy, preview, versions, image actions, and draft sync.
- Added workspace page with editor, preview, versions, image plan, autosave, conflict handling, and sync confirmation.
- Added backend safe projection for `accountId` and `accountName` on working-copy responses.
- Added backend read-only `GET /wechat-articles/{article_id}/draft-sync-context?article_version_id=...`.
- Reused existing readiness validation for draft sync context instead of inventing a parallel rule set.

## Changed files

- `backend/app/api/wechat_articles.py`
- `backend/app/schemas/wechat_article.py`
- `backend/app/services/publishing.py`
- `backend/tests/test_wechat_article_api.py`
- `frontend/src/App.tsx`
- `frontend/src/appRoutes.ts`
- `frontend/src/appRoutes.test.ts`
- `frontend/src/services/wechatArticle.ts`
- `frontend/src/services/wechatArticle.test.ts`
- `frontend/src/pages/WechatArticleWorkspace.tsx`
- `frontend/src/pages/WechatArticleWorkspace.test.tsx`
- `frontend/src/components/wechat-article/ArticleEditor.tsx`
- `frontend/src/components/wechat-article/ArticleImageSlot.tsx`
- `frontend/src/components/wechat-article/ArticleVersionConflict.tsx`
- `frontend/src/components/wechat-article/WechatSyncConfirmation.tsx`
- `frontend/src/styles/wechat-article-workspace.css`

## TDD evidence

### Backend RED -> GREEN

- RED: added API tests for:
  - working-copy account projection on create/get/patch
  - cross-scope and version mismatch returning 404
  - no-history sync context returning allowlist-safe `remote = null`
  - same-article/account/org mapping and job projection
- GREEN:
  - command: `.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_wechat_article_api.py -q`
  - result: `11 passed in 7.23s`

### Frontend RED -> GREEN

- RED: service tests for allowlist parsing, structured 409, upload payload, image selection payload, sync payload.
- RED: page tests for:
  - 2-second debounce
  - stale save response
  - prompt hidden/reveal/copy
  - generate-all, single, retry, upload, select
  - stable slot preservation
  - 409 conflict banner and all three actions
  - sync confirmation payload, cancel, unavailable, remote conflict/reconciliation blocking
  - 390 layout semantics and live region
- GREEN:
  - command: `npm.cmd --prefix frontend test -- --run src/services/wechatArticle.test.ts src/pages/WechatArticleWorkspace.test.tsx src/appRoutes.test.ts`
  - result: `3 passed`, `26 passed`

### Notable RED fixes

- `workingCopyQuery.isError` had to win over loading state to avoid error + fetching rendering as indefinite spinner.
- autosave tests needed local fake timers only, with real timers restored in `finally`.
- `基于新版本继续修改` originally refetched and overwrote local document. Fixed to preserve local document, update expected lock only, and wait for a fresh edit before save.
- `放弃本地修改` originally restored local in-memory state. Fixed to refetch server copy and restore server document.
- sync modal focus implementation moved away from timer-driven behavior toward ref-driven safe action plus `afterOpenChange` for focus return.

## Behavior delivered

- Autosave is debounced by 2 seconds and never auto-retries a 409 overwrite.
- Conflict actions now behave as follows:
  - `查看差异`: opens the versions surface and shows local/server comparison.
  - `基于新版本继续修改`: preserves local document, updates expected lock to latest server lock, and does not auto-save.
  - `放弃本地修改`: refetches and restores latest server document.
- Image plan keeps prompts hidden until explicit retrieval.
- Image plan supports generate-all, single generate, retry generate, upload, and selection.
- Stable slots preserve selected material across refresh.
- Sync confirmation shows target account, article title, immutable version, image count, blockers, warnings, remote status, remote operation type, and remote error code.
- Remote conflict/reconciliation state blocks blind confirmation.
- Sync path only uses Task 13 draft sync, never `freepublish`.

## Verification

### Passed

- `npm.cmd --prefix frontend test -- --run src/services/wechatArticle.test.ts src/pages/WechatArticleWorkspace.test.tsx src/appRoutes.test.ts`
- `.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_wechat_article_api.py -q`
- `npm.cmd --prefix frontend run lint`
- `npm.cmd --prefix frontend run build`
- `git diff --check`
- `node C:\Users\AKSSINA\.agents\skills\impeccable\scripts\detect.mjs --json frontend/src/pages/WechatArticleWorkspace.tsx frontend/src/components/wechat-article/ArticleEditor.tsx frontend/src/components/wechat-article/ArticleImageSlot.tsx frontend/src/components/wechat-article/ArticleVersionConflict.tsx frontend/src/components/wechat-article/WechatSyncConfirmation.tsx frontend/src/styles/wechat-article-workspace.css`
  - result: `[]`
- secret scan:
  - command: `rg -n --hidden --glob '!node_modules/**' --glob '!dist/**' --glob '!frontend/dist/**' "(AKIA[0-9A-Z]{16}|-----BEGIN (RSA|EC|OPENSSH|DSA) PRIVATE KEY-----|xox[baprs]-|ghp_[A-Za-z0-9]{36,}|AIza[0-9A-Za-z\\-_]{35})" .`
  - result: no matches

### Browser acceptance

- Per brief, browser validation was time-bounded to one round.
- What was validated:
  - local dev server booted and page shell loaded in a real browser
  - mocked runtime showed a real navigation anomaly for deep link entry
- What failed during time-bounded browser run:
  - direct navigation to `http://127.0.0.1:4173/wechat-articles/9` resolved to `/` in the real browser session instead of staying on the workspace route
  - observed extra 404s for `/api/notifications/unread-count` and `/api/skills?platform=douyin&surface=composer` in the mocked browser session
- Because the browser time budget expired, I stopped further investigation and did not start another browser toolchain.
- No trustworthy screenshot artifact was produced for final acceptance because the route did not remain on the target page.

## Accessibility review

- Covered by tests:
  - semantic buttons and labeled editor fields
  - live-region announcements
  - sync confirmation close focus return
  - remote conflict blocking disables confirm
  - narrow layout semantic structure at 390 width
- Partially validated only in jsdom, not fully browser-confirmed:
  - initial dialog focus entering the first safe action in a real browser

## Security and contract review

- Backend sync context is read-only and does not create jobs, intents, or external side effects.
- Sync context validates article, version, account, and org scope.
- Response remains allowlist-safe and does not expose raw HTML, token state, publish package, approval snapshot, or raw provider error payloads.
- Working-copy account projection stays under existing visibility checks.

## Concurrency / state integrity review

- Save pipeline uses token-based stale-response protection.
- Conflict state is explicit and prevents silent overwrite.
- Local document preservation across `基于新版本继续修改` is now deliberate, not incidental.
- Stable image slot merge keeps user-selected material through refresh.

## Residual concerns

- Browser acceptance did not complete successfully because real-browser deep-link entry to `/wechat-articles/9` redirected to `/`. Static tests and build are green, but this route still needs runtime confirmation before deployment.
- jsdom emits `getComputedStyle(..., pseudo)` warnings from Ant Design modal internals during workspace tests; tests still pass and behavior assertions remain green.
- `vite build` still reports existing large-chunk warnings unrelated to this task's logic.

## Fix Round 1 - Remote conflict fail-closed

### Review finding addressed

- `draft-sync-context` previously surfaced `remote.status` but did not fold `wechat_conflict` / `wechat_reconciliation_required` into `readiness`.
- The sync modal and submit path trusted `readiness.canSync` alone, so a malformed backend response could still permit blind retry.

### Files updated in fix round

- `backend/app/services/publishing.py`
- `backend/tests/test_wechat_article_api.py`
- `frontend/src/services/wechatArticle.ts`
- `frontend/src/services/wechatArticle.test.ts`
- `frontend/src/components/wechat-article/WechatSyncConfirmation.tsx`
- `frontend/src/pages/WechatArticleWorkspace.tsx`
- `frontend/src/pages/WechatArticleWorkspace.test.tsx`

### RED -> GREEN evidence

- Backend RED:
  - command: `.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_wechat_article_api.py -k "draft_sync_context and conflict_states" -q`
  - result before fix: `2 failed`
  - failure: `response.json()["readiness"]["canSync"]` stayed `true` for both `wechat_conflict` and `wechat_reconciliation_required`
- Backend GREEN:
  - same command after fix
  - result: `2 passed, 11 deselected`
- Frontend RED:
  - command: `npm.cmd --prefix frontend test -- --run src/pages/WechatArticleWorkspace.test.tsx -t "fails closed when remote draft state requires manual conflict review even if canSync is malformed"`
  - result before fix: new test failed because the modal still rendered an enabled confirm path without fail-closed manual-review handling
- Frontend GREEN:
  - same command after fix
  - result: `1 passed, 15 skipped`

### Fix detail

- Backend now maps real Task 13 statuses:
  - `wechat_conflict` -> blocker `REMOTE_DRAFT_CONFLICT`
  - `wechat_reconciliation_required` -> blocker `REMOTE_DRAFT_RECONCILIATION_REQUIRED`
- Those statuses force `readiness.canSync = false` in the read-only sync-context projection while preserving article/account/version scope and keeping the endpoint side-effect free.
- Frontend now uses shared guards in service code so both UI state and submit logic fail closed:
  - modal confirm button disables for manual-review statuses even if `readiness.canSync` is malformed
  - submit function returns early for the same states
  - modal shows explicit conflict / manual-reconciliation guidance

### Additional verification after fix

- `npm.cmd --prefix frontend test -- --run src/services/wechatArticle.test.ts src/pages/WechatArticleWorkspace.test.tsx src/appRoutes.test.ts`
  - result: `3 passed`, `28 passed`
- `.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_wechat_article_api.py backend/tests/test_wechat_draft_sync.py -k "draft_sync_context or WECHAT_CONFLICT or WECHAT_RECONCILIATION_REQUIRED or WECHAT_BLOCKED" -q`
  - result: `7 passed, 37 deselected`
- `npm.cmd --prefix frontend run lint`
  - result: passed
- `npm.cmd --prefix frontend run build`
  - result: passed, existing large-chunk warning only
- `node C:\Users\AKSSINA\.agents\skills\impeccable\scripts\detect.mjs --json frontend/src/pages/WechatArticleWorkspace.tsx frontend/src/components/wechat-article/WechatSyncConfirmation.tsx frontend/src/services/wechatArticle.ts frontend/src/services/wechatArticle.test.ts frontend/src/styles/wechat-article-workspace.css`
  - result: `[]`

### Deep-link recheck

- Time-bounded retest on Wednesday, August 12, 2026 used `vite preview` plus Playwright direct navigation to `http://127.0.0.1:4173/wechat-articles/9`.
- Result:
  - initial document request for `/wechat-articles/9` returned `200`
  - final browser URL became `/login`
  - page rendered the login screen, not the workspace
- Conclusion: the remaining deep-link issue is application runtime redirect behavior, not the Playwright navigation script itself. I stopped after this single bounded retest and shut the preview server down.
