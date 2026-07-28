# Task 16 fix round 2 — Artifact version chains

## Outcome

- Replaced the one-revision projection map with a source Artifact ID to ordered persisted revision chain map.
- An action on any rendered chain version now validates the returned Artifact scope, ID, and version before updating that exact version or appending the next version to the original source chain.
- Turn rendering validates every chain element against the verified source Artifact: account, thread, Turn, artifact type, unique identity, and strict sequential versions. Invalid chains fail closed.
- A refresh failure after a verified load keeps the last-known-good source and its validated revision chain readable, while exposing a retryable update-failure state. First loads still fail closed.

## Regression coverage

- Rejected a mismatched returned revision chain without rendering it.
- Preserved V1 and V2 after a failed source refresh.
- Updated the exact V2 card after acceptance so its status and actions changed immediately.
- Preserved V2 and appended V3 when V2 was revised.

## Verification

- `npm.cmd --prefix frontend test -- --run src/components/brain/ArtifactCard.test.tsx src/components/brain/TurnStream.test.tsx src/pages/BrainHome.test.tsx src/api/brain.test.ts` — 4 files, 79 tests passed.
- `npm.cmd --prefix frontend run build` — TypeScript check and production build passed.
- `git diff --check` — passed.

The focused suite retains pre-existing JSDOM/React Router warnings; it exits successfully.
