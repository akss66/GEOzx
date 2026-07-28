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

## Review fix round 1

- Revision payloads now preserve the persisted `title` and `summary` alongside
  the Artifact sections. This matches the backend revision validator, which
  selects fields for the concrete deliverable schema; in particular it restores
  the required `ReviewReportPayload.summary` field.
- “Adopt and create next step” keeps its intent inside the acceptance mutation
  and only pre-fills the conversation after the exact Artifact acceptance
  succeeds. A failed acceptance leaves the existing draft unchanged.
- The revision response is retained by source Artifact ID. The source V1 stays
  readable, while the exact persisted V2 returned by the endpoint is displayed
  as the latest revision after matching account, thread, and Turn ownership.
- Presentation now projects titles, summaries, nested object keys, and quality
  critic content through a Chinese business-safe allowlist. Unsafe raw/internal
  content and English internal keys are hidden.

Additional RED/GREEN coverage covers the exact ReviewReport request body,
adoption success/failure timing, returned V1-to-V2 version display, and nested
content sanitization.

The final focused verification for the fix round ran all four Task 16 suites:
`75 tests passed`, followed by `pnpm.cmd exec tsc --noEmit` and `git diff --check`.
