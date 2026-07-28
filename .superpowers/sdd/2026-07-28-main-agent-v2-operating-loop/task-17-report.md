# Task 17 Report — Account Artifact Center

## Delivered

- Added `ArtifactCenter` as the secondary results mode in BrainHome.
- Lists paginated account artifacts through `listArtifacts` with account-scoped query keys and server type/status filters.
- Rejects any returned item whose `account_id` differs from the selected account before it can render or open.
- Provides date filtering on the loaded page and labels the scope as `Current page only`.
- Handles account-unavailable, loading, empty, error/retry, pagination, and account-switch reset states.
- Reuses `ArtifactCard` and the established accept/revise action flow for the selected API Artifact; mutations invalidate the exact account artifact query prefix.
- Return-to-source requires the Artifact's same-account `thread_id` and `turn_id`, loads that specific thread, validates the exact Turn, then scrolls and focuses `[data-turn-id]`. Failed/missing provenance leaves the user in results with retry; no task-global/latest fallback is used.

## TDD evidence

1. Added `ArtifactCenter.test.tsx` before the component. The focused test command first failed because `./ArtifactCenter` did not exist.
2. Added BrainHome coverage for mode switch, shared card, exact source Turn focus, and retryable failed source load.
3. Added a dedicated failed-source retry assertion, then verified it returns to `conversation-turn-101` after retry.

## Verification

- `pnpm.cmd exec vitest run src/components/brain/ArtifactCenter.test.tsx src/components/brain/ArtifactCard.test.tsx src/api/brain.test.ts src/pages/BrainHome.test.tsx` — 4 files, 74 tests passed.
- `pnpm.cmd exec tsc --noEmit` — passed.
- `pnpm.cmd build` — passed.
- `git diff --check` — passed.

## Review fix round 2

- Replaced the result-type selector's obsolete client-only values with the complete set of backend-allowed artifact codes: `account_inspection_report`, `positioning_strategy`, `topic_plan`, `publish_calendar`, `video_script`, `art_prompt`, `video_asset`, `edited_video`, `review_report`, `ad_plan`, and `cs_record`.
- Each allowed code has a professional Chinese label while the select value remains the API code. Unknown returned artifact types continue to render as the safe Chinese fallback “其他成果” and cannot become a filter option.
- Extended table-driven selector coverage to assert every displayed option and its exact `listArtifacts` request code. Acceptance and revision coverage now stubs the refreshed second list response and asserts the rendered row's status/version (`已采用 V1` and `修改中 V2`), not just a refetch call count.

### Review round 2 verification

- `pnpm.cmd exec vitest run src/api/brain.test.ts src/components/brain/ArtifactCard.test.tsx src/components/brain/ArtifactCenter.test.tsx src/pages/BrainHome.test.tsx` — 4 files, 80 tests passed.
- `pnpm.cmd exec tsc --noEmit` — passed.
- `pnpm.cmd build` — passed.
- `git diff --check` — passed.

The existing jsdom suite still emits its known React Router, Ant Design resize-observer, and pseudo-element warnings; no test failed.

## Review fix round 1

- The selected result detail now changes only after the accept/revision response passes the exact artifact identity check; it uses the accepted or V2 payload returned by the API and preserves the existing detail on a failed or mismatched response.
- The account results component is keyed by account. A new account begins at page 1 with empty server filters, while delayed responses from an earlier account cannot render, select, or alter the new account state.
- Return-to-source now fails closed for a wrong account, wrong thread, or missing source Turn. Each outcome remains in the results center, shows a Chinese retryable error, and retry forces a fresh conversation request even when a previous request technically succeeded with invalid provenance.
- Product-facing labels, filters, pagination, date scope, and statuses are now professional Chinese. Artifact type options are stable business choices with their server codes preserved as option values; unknown server types render as “其他成果”.
- Added regressions for exact accept/revision detail updates and list refresh, account switch with a deferred earlier response, and wrong-account/wrong-thread/missing-Turn source retries.

### Review verification

- `pnpm.cmd exec vitest run src/api/brain.test.ts src/components/brain/ArtifactCard.test.tsx src/components/brain/ArtifactCenter.test.tsx src/pages/BrainHome.test.tsx` — 4 files, 80 tests passed.
- `pnpm.cmd exec tsc --noEmit` — passed.
- `pnpm.cmd build` — passed.
- `git diff --check` — passed.
