# Task 16 fix round 3 — Superseded Artifact versions

## Outcome

- A successful revision marks the exact acted-on persisted version as `superseded` before retaining the returned next version in the same source chain.
- Root V1 revisions retain a validated local V1 status override, so an old source fetch or a later refresh failure cannot restore V1 actions.
- V1 and intermediate revisions no longer offer adoption or revision actions; the newest returned version remains actionable.
- A revision-chain validation failure now exposes a retry button even when the source Artifact itself is ready.

## Regression coverage

- V1 → V2: V1 is marked updated and has no adoption/revision actions; V2 remains actionable.
- V2 → V3: V2 is marked updated and has no adoption/revision actions; V3 remains actionable.
- A failed source refresh retains the local superseded V1 override and validated V2 chain.
- A mismatched revision chain remains fail-closed and includes retry.

## Verification

- `npm.cmd --prefix frontend test -- --run src/components/brain/ArtifactCard.test.tsx src/components/brain/TurnStream.test.tsx src/pages/BrainHome.test.tsx src/api/brain.test.ts` — 4 files, 79 tests passed.
- `npm.cmd --prefix frontend run build` — TypeScript check and production build passed.
- `git diff --check` — passed.

The focused suite retains existing JSDOM/React Router warnings; it exits successfully.
