# Task 15 Report: Render Conversation History by Source Turn

## Outcome

Added `TurnStream` for the V2 conversation response path. It renders the
server-provided `ConversationThread.turns` in order, and renders projections
only when their `turn_id` matches the enclosing Turn.

## TDD evidence

- RED: `pnpm.cmd exec vitest run src/components/brain/TurnStream.test.tsx`
  failed because `./TurnStream` did not exist.
- GREEN: the same test passed after the minimal component implementation.
- Added BrainHome integration coverage for V2 source-Turn ownership and
  account-change stale-Thread isolation.

## Coverage

- Artifact stays in its source Turn; a later greeting and retry do not receive
  it.
- Server Turn order is retained and Turn/projection render identities use
  durable IDs.
- Unknown projection kinds render one compact safe fallback without raw JSON
  or technical detail disclosure.
- The V2 path does not append task-global legacy acceptances/Artifacts.
- The legacy runtime renderer remains in place and its existing behavior is
  exercised by the BrainHome suite.
- Switching accounts hides a stale Thread before the selected account's Thread
  is loaded.

## Verification

From `frontend`:

- `pnpm.cmd exec vitest run src/components/brain/TurnStream.test.tsx src/pages/BrainHome.test.tsx`
  - 2 files, 38 tests passed.
- `pnpm.cmd exec tsc --noEmit`
  - passed.
- `git diff --check`
  - passed.

The existing jsdom test environment continues to print React Router and Ant
Design environment warnings; neither represents a test failure.
