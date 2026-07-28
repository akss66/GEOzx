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

The existing jsdom suite still emits its known React Router, Ant Design resize-observer, and pseudo-element warnings; no test failed.
