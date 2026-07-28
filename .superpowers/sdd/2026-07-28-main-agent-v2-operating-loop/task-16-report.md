# Task 16 Report: Present Artifacts as Business Outcomes

## Outcome

The V2 conversation stream now renders persisted formal `Artifact` records as
business-facing report cards. A card is bound to the exact source Turn and
fails closed if the artifact, account, thread, or Turn identity does not match.
It never falls back to the projection's raw report content.

The card presents a Chinese business summary, core conclusion, data period,
key data, issues, recommendations, participating experts, and a collapsed
evidence/quality view. Internal keys, checklists, execution logs, and raw
debug-like content are suppressed.

## Actions

- View the complete report in place.
- Adopt the report through the Artifact acceptance endpoint.
- Adopt and pre-fill a visible next-step conversation request; no external
  execution is started.
- Submit a concrete revision note through the Artifact revision endpoint.

Failed actions retain a usable card and surface an error rather than showing
optimistic success. While a revision is pending, the current V1 stays visible
and the UI separately states that V2 is being generated.

## TDD coverage

- Began with a failing component test for the missing Artifact card.
- Added a failing exact Artifact API contract test.
- Added a failing ownership-mismatch test (including active Thread account
  mismatch) to prove fail-closed rendering.
- Added a failing refresh test to prove a successful action reloads the exact
  persisted Artifact instead of reusing stale projection data.

## Verification

From `frontend`:

```text
pnpm.cmd exec vitest run src/api/brain.test.ts src/components/brain/ArtifactCard.test.tsx src/components/brain/TurnStream.test.tsx src/pages/BrainHome.test.tsx
69 tests passed

pnpm.cmd exec tsc --noEmit
passed
```

From the repository root:

```text
git diff --check
passed
```
