# Task 17 Report

Date: 2026-08-12
Workspace: `C:\Users\AKSSINA\Desktop\Workplace\GEOzx\.worktrees\wechat-official-agent`
Baseline: `2c063f0c2b4bfa239fe8862e6e206b7b633fc747`

## Scope delivered

- Projected WeChat article production onto the existing single WorkTurn pipeline.
- Added a semantic article workspace handoff action sourced only from a valid same-turn durable `article_id`.
- Preserved truthful blocked/downstream assistant copy while keeping the workspace recovery link available.
- Restored WeChat paused WorkTurns after refresh in the same turn slot.
- Cleared previous-account conversation/article caches on account switch to prevent cross-account leakage.
- Kept technical detail inside the existing collapsed disclosure and reused the existing Thinking Orb / stream / runtime path.
- Added a mock Playwright spec file for the cross-page WeChat article flow.

## Files changed

- `frontend/src/types.ts`
- `frontend/src/components/brain/workTurnProjection.ts`
- `frontend/src/components/brain/workTurnProjection.test.ts`
- `frontend/src/components/brain/workTurnPresentation.ts`
- `frontend/src/components/brain/WorkTurnCard.tsx`
- `frontend/src/components/brain/WorkTurnCard.test.tsx`
- `frontend/src/pages/BrainHome.tsx`
- `frontend/src/pages/BrainHome.test.tsx`
- `frontend/e2e/wechat-article-flow.spec.ts`

## TDD evidence

### RED -> GREEN 1: same-turn durable handoff link

Initial failure:

- `cd frontend`
- `npm.cmd test -- --run src/components/brain/workTurnProjection.test.ts`
- RED after semantic change: the initial WeChat handoff test failed because `assistant_response` incorrectly overrode the business handoff title.

Fix:

- Initial `article_action` handoff now prefers `articleWorkspaceAction.title`.
- Downstream and blocked states keep truthful `assistant_response`.

GREEN:

- `npm.cmd test -- --run src/components/brain/workTurnProjection.test.ts`
- Result: `32 passed`

Added proof:

- `keeps a WeChat article action interrupt in the same waiting WorkTurn...`
- `rejects cross-turn and invalid WeChat article ids...`
- `keeps the article workspace link after handoff when the same turn continues into downstream WeChat work`

### RED -> GREEN 2: truthful blocked recovery states

Added failing projection assertions for:

- image provider unavailable
- draft reconciliation / sync conflict

GREEN after projection fix:

- `keeps the article workspace link for blocked WeChat recovery states without replacing truthful failure copy`
- `keeps the article workspace link for draft reconciliation without replacing truthful conflict copy`

These prove:

- no false success copy
- recovery still points to `/wechat-articles/:articleId`
- same turn keeps its durable article identity

### RED -> GREEN 3: durable WeChat stage mapping

Added failing runtime-step expectations for:

- `generate_images`
- `sync_draft`
- `draft_sync_completed`
- unknown step fallback

GREEN:

- `maps downstream WeChat stages only when durable step codes exist and still falls back safely`
- `maps durable WeChat stages into business language and falls back safely for unknown runtime steps`

Implementation notes:

- `workTurnProjection.ts` adds only the brief-approved stage labels.
- `workTurnPresentation.ts` now prefers active steps before waiting steps so downstream active work is surfaced correctly.

### RED -> GREEN 4: BrainHome refresh recovery and account isolation

Existing BrainHome generic recovery machinery was reused; only the nearest WeChat fixture/tests were added.

Added tests:

- `recovers a WeChat article action pause into the same WorkTurn after refresh`
- `clears the previous account conversation cache and article link after switching accounts`

Failure root cause:

- previous-account thread cache was not removed on account switch, so the stale article link could survive in cache.

Fix:

- `BrainHome.tsx` now removes `["brain-conversation", previousThreadId]` alongside the prior account artifact/pending-work caches.

## Behavior summary

- Initial WeChat draft completion now renders inside the existing WorkTurn as `文章初稿已生成` plus the semantic `打开文章工作台` link.
- The link is shown only when a valid positive durable `article_id` exists on the same turn/skill run.
- Downstream work such as image generation or draft sync keeps the same workspace link without creating a second assistant card.
- Provider-unavailable and reconciliation-conflict states keep truthful blocked copy and still expose the article workspace as the recovery path.
- Account switching removes previous-account thread/article projection state so another account cannot inherit the old article link.

## Accessibility / UI checks

- `WorkTurnCard` renders the business action as a semantic `<a>` link.
- Technical detail remains collapsed by default.
- The existing WorkTurn structure, right-top controls, and Thinking Orb path were preserved.

## Verification

Executed successfully:

- `cd frontend && npm.cmd test -- --run src/components/brain/workTurnProjection.test.ts`
- `cd frontend && npm.cmd test -- --run src/components/brain/workTurnProjection.test.ts src/components/brain/WorkTurnCard.test.tsx src/pages/BrainHome.test.tsx`
- `cd frontend && npm.cmd test -- --run src/components/brain/TurnStream.test.tsx src/components/brain/WorkTurnCard.test.tsx src/components/brain/workTurnPresentation.test.ts src/components/brain/workTurnProjection.test.ts src/pages/BrainHome.test.tsx`
- `cd frontend && npm.cmd test`
- `cd frontend && npm.cmd run lint`
- `cd frontend && npm.cmd run build`
- `cd frontend && npm.cmd run check:main-agent-bundle`
- `git diff --check`

Secret-pattern scan on changed files:

- `rg -n "access_token|api[_-]?key|secret|BEGIN (RSA|OPENSSH|PRIVATE KEY)|sk-[A-Za-z0-9]" ...`
- Only expected matches were existing type fields in `types.ts` and a mock token string in `frontend/e2e/wechat-article-flow.spec.ts`.

## E2E mock status

Created:

- `frontend/e2e/wechat-article-flow.spec.ts`

Collection/list proof:

- `cd frontend && npm.cmd run test:e2e -- --list wechat-article-flow.spec.ts`
- Result: `Total: 1 test in 1 file`

Runtime limitation encountered:

- `npm.cmd run test:e2e -- wechat-article-flow.spec.ts`
- The environment entered a blank-page path before the mocked login/home UI became interactive.
- First timeout: `waiting for locator('.tz-account-trigger')`
- After removing the incorrect forced `/brain` navigation, second timeout: `waiting for locator('input[autocomplete=\"email\"]')`

Per task instruction, the browser/tooling pass stopped after that bounded fix pass and the limitation is reported as a concern.

## Self-review

- The UI change stays inside the existing WorkTurn path and does not introduce a second runtime or parallel state store.
- The WeChat action link is tied to the same-turn durable artifact and not exposed for invalid/cross-turn IDs.
- Account isolation fix is intentionally narrow: it removes the previous thread cache only when the account actually changes.
- Main residual risk is E2E environment boot behavior; unit/integration coverage around projection and BrainHome state is strong, but the new Playwright spec did not complete end-to-end in this environment.

## Commit

- Commit SHA: recorded in the final task return for this atomic commit
- Commit message: `feat: surface WeChat production in main agent`

## Concerns

- `frontend/e2e/wechat-article-flow.spec.ts` collects successfully but did not complete in the current Playwright environment due the blank-page/login-entry timeout described above.

## Fix Round 1 Append

Date: 2026-08-12

### Contract cleanup delivered

- Added backend public projection `wechat_article_workspace` and kept it separate from generic artifact payloads.
- Removed frontend dependence on legacy `article_action` interrupts.
- Removed frontend fallback that read `artifact.report.article_id`; workspace handoff now requires the dedicated `wechat_article_workspace` projection.
- Removed fake WeChat stage-label fallback for `generate_images`, `sync_draft`, and `draft_sync_completed`; downstream activity now comes from `wechat_article_workspace.current_action` or truthful assistant/runtime copy.
- Kept `TurnInterruptKind` restricted to `clarification | approval | manual_pause`.

### Files touched in fix round

- `backend/app/api/conversations.py`
- `backend/app/schemas/conversation.py`
- `backend/tests/test_conversation_api.py`
- `frontend/src/types.ts`
- `frontend/src/components/brain/workTurnProjection.ts`
- `frontend/src/components/brain/workTurnProjection.test.ts`
- `frontend/src/components/brain/TurnStream.tsx`
- `frontend/src/pages/BrainHome.test.tsx`
- `frontend/e2e/wechat-article-flow.spec.ts`

### RED -> GREEN evidence

Backend:

- RED first via new conversation API assertions for same-turn lineage, allowlist-only projection, and fail-closed scope/account checks.
- GREEN: `.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_conversation_api.py -q`
- Result: `55 passed in 28.12s`

Frontend:

- RED after contract cleanup: `workTurnProjection` failed on old tests that still expected `artifact.report` and fake downstream runtime step labels.
- GREEN after migrating fixtures to `wechat_article_workspace` and removing the last `sync_draft` fake runtime step from line-level fixture coverage.
- GREEN: `npm.cmd --prefix frontend test -- --run src/components/brain/workTurnProjection.test.ts src/components/brain/TurnStream.test.tsx src/pages/BrainHome.test.tsx`
- Result: `125 passed`

Static/build gates:

- `npm.cmd --prefix frontend run lint`
- `npm.cmd --prefix frontend run build`
- `npm.cmd --prefix frontend run check:main-agent-bundle`
- `git diff --check`

All passed in this fix round.

### E2E attempt record

Per instruction, only bounded attempts were made and no further E2E retries were run afterward.

Attempt 1:

- Command: `PLAYWRIGHT_PORT=4176 npm.cmd --prefix frontend run test:e2e -- wechat-article-flow.spec.ts --project=chromium --workers=1`
- Result: failed before first business assertion.
- Observed boundary: after switching to the WeChat account from `/accounts`, a full `page.goto("/")` returned the shell to Douyin context (`抖 / 选择抖音账号`).
- Root cause inferred from code and runtime: `frontend/src/stores/currentWorkspace.ts` still restores persisted platform as Douyin on reload, so a full-page reload does not preserve `wechat_official_account`.

Attempt 2:

- After converting the test-side return path to SPA navigation, Playwright aborted during test bootstrap with `Playwright Test did not expect test() to be called here`.
- This was a runner/tooling failure, not a reached product assertion.

Net result:

- The spec did not reliably reach the first business assertion in the current environment.
- Latest evidence points to the application-side workspace persistence bug on full reload as the main product blocker for the intended deep-link/reload path.

### Secret-pattern scan

- Ran `rg` over changed files for `access_token|api[_-]?key|secret|BEGIN ...|sk-...`.
- Matches were limited to expected existing type fields, security-redaction tests, and the local mock login token in `frontend/e2e/wechat-article-flow.spec.ts`.
- No unexpected secret material was introduced.

### Fix Round 1 final browser evidence

- The bounded E2E attempt exposed an application defect rather than a mock-only issue: `readStoredWorkspace()` restored every persisted non-Douyin platform as Douyin.
- Added RED/GREEN coverage in `frontend/src/stores/currentWorkspace.test.ts` for restoring `wechat_official_account` after reload and failing closed to Douyin for unknown platform values.
- Updated `frontend/src/stores/currentWorkspace.ts` to restore only an explicit `Platform` allowlist.
- Fresh focused regression: 135 passed across the workspace store, WorkTurn projection, TurnStream and BrainHome.
- Fresh E2E command with `CI=1`, `PLAYWRIGHT_PORT=5189`, and one worker reached every business assertion. Its first run failed only because expected `event-stream` requests were not included in the mock allowlist; after adding that deterministic mock route, the same command passed 1/1 in 8.8 seconds.
- The passing E2E covered article creation, a single in-place WorkTurn handoff, reload recovery, semantic article-workspace link, generate-all, prompt reveal, version conflict, preview, explicit draft confirmation, draft sync, and account switching without cross-account remnants.
- The local mock intentionally has no realtime backend process, so Vite logged expected WebSocket proxy `ECONNREFUSED` messages while the HTTP/durable recovery assertions still passed.
