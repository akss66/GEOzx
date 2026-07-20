# Identity Task 6 Report

## Scope

- Rebuilt the desktop member-governance workbench in `frontend/src/pages/Users.tsx` and focused modules under `frontend/src/components/users/`.
- Reworked `frontend/src/styles/user-workspace.css` for the approved warm-paper, dense desktop workspace.
- Added interaction coverage in `frontend/src/pages/Users.test.tsx` for the original Task 6 acceptance paths and review findings.
- Added a wire-response compatibility type and normalization in `frontend/src/types.ts` and `frontend/src/api/auth.ts` because an authenticated live backend returned an access catalog without `accounts`.
- Did not modify backend code or model-governance flows.

## Execution Boundary

- Entry point: `frontend/src/pages/Users.tsx`.
- Server state: React Query owns the member roster, per-member detail, normalized access catalog, and current administrator secondary-password status.
- API boundary: `getUserAccessCatalog` converts missing or non-array `clients`, `projects`, and `accounts` collections to empty arrays. Missing account `project_ids` is also normalized to an empty array. The canonical UI type still requires all collections.
- Local state: member tabs own their form drafts and feedback. `MemberAccess` and `MemberSecurity` are keyed by member ID, so member changes remount member-owned drafts. `PermanentDeletePanel` is keyed inside the security subtree.
- Access draft baseline: unrelated detail refetches do not overwrite a draft. Only a successful access save advances the saved baseline; changing member discards the old member's draft.
- Successful deletion updates the roster only after the API succeeds, removes the deleted detail query, and selects the adjacent member. This is not an optimistic deletion.

## New Member Flow

- The existing modal now contains identity/login and initial resource authorization in one flow.
- Normal members are created sequentially with `createUser` followed by `updateUserAccess`; the test asserts this call order.
- If access assignment fails, the created identity remains. The modal stays open, clears and locks the initial password and identity fields, states that no automatic rollback occurred, and retries only `updateUserAccess`.
- The new member is added to the roster and selected before access recovery, so closing the partial-result modal still leaves a real recovery path in the resource-permissions tab.
- Administrators receive global access from the existing backend role behavior and skip scoped access assignment.

## TDD Evidence

- Review fixes from the preceding pass were developed from five failing interaction cases, later split into 14 focused Users tests.
- Current RED: `npm.cmd test -- src/pages/Users.test.tsx src/api/auth.test.ts` failed 6 of 28 tests before production changes. Failures covered legacy catalog normalization, the no-accounts Users crash, three missing new-member authorization/recovery paths, and access dirty-state clearing. The member-switch draft test already passed because the keyed subtree behavior was present.
- Current GREEN: the same two-file run passed 28 of 28 after implementation.
- Independent focused Users verification passed 19 of 19.

Exact `Users.test.tsx` tests:

1. `auto-selects the first member and exposes the four governance tabs`
2. `renders with the legacy access catalog shape when accounts are omitted`
3. `creates a scoped member with initial resource authorization in one flow`
4. `recovers from a partial create result without creating the identity twice`
5. `creates an administrator without requiring scoped grants`
6. `filters the roster by search, role, anomaly, and status`
7. `saves identity changes and translates enable-disable business errors`
8. `toggles account scope, persists selected accounts, and explains when no accounts are visible`
9. `sets the current admin secondary password and resets the selected member password`
10. `recovers from stale delete previews and clears sensitive destructive inputs`
11. `clears destructive inputs when the delete flow closes`
12. `removes a permanently deleted member from the roster and selects the next member`
13. `isolates security drafts and deletion previews when the selected member changes`
14. `renders global access as read-only and blocks scoped access saves`
15. `preserves unsaved access drafts across overview save and status refetches`
16. `drops an unsaved access draft when the selected member changes`
17. `translates stable business errors from overview saves`
18. `translates generic 422 errors from overview saves`
19. `enables permanent deletion only after exact email and secondary password confirmation`

The targeted API regression is `normalizes legacy access catalogs that omit optional collections` in `frontend/src/api/auth.test.ts`.

## UX And Accessibility Review

- The first visible member is selected automatically; roster search and filters remain local to the page.
- Global-access members see explicit read-only copy and an empty catalog state when legacy responses omit accounts; scoped mutation controls are absent.
- Missing catalog collections render as honest empty states. No placeholder clients, projects, or accounts are created.
- Initial authorization uses labeled fieldsets, checkboxes, role selects, and named account-scope radios. The partial-result message uses an alert role and retry remains keyboard-operable.
- Dangerous and read-only states include explicit text and do not rely on color alone.
- A real authenticated browser accessibility-tree, focus-order, and visual pass was not rerun in this session because no authenticated browser/Chrome DevTools session is available. jsdom interaction coverage is not claimed as an end-to-end replacement.

## Security Review

- Security/login drafts, deletion confirmation fields, feedback, preview data, and preview tokens cannot carry from member A to member B because the complete security subtree remounts on member ID changes.
- `preview_token`, email confirmation, current password, secondary password, and new login password remain component-local and are not placed in query keys, browser storage, URL state, or persistent caches.
- The create flow clears the initial login password immediately after identity creation and on modal close/success. A partial access failure never resubmits `createUser`.
- Permanent deletion clears email and secondary-password inputs on close, success, and every failure. Stale, expired, invalid, or used previews also clear preview state.
- Stable governance error codes map to business copy; raw response bodies are not rendered.

## Contract Notes

- Current repository backend code declares `clients`, `projects`, and `accounts` as required in `UserAccessCatalogOut` and returns all three from `get_access_catalog`.
- Browser acceptance showed a deployed or legacy backend response without `accounts`. The frontend now models that wire response as optional collections and normalizes it at the API boundary while retaining a strict canonical UI contract.
- Treating an omitted collection as empty avoids a crash and does not fabricate data, but it cannot distinguish a legacy unsupported collection from a legitimately empty catalog.
- There is no member-level audit endpoint in the current frontend contract, so the activity tab shows an honest unavailable state.
- The secondary-password contract does not expose attempts, and the member list does not expose aggregate lock state; those values remain unavailable rather than invented.

## Verification

- Focused Users: `npm.cmd test -- src/pages/Users.test.tsx` - passed, 1 file / 19 tests.
- Targeted Users + API: `npm.cmd test -- src/pages/Users.test.tsx src/api/auth.test.ts` - passed, 2 files / 28 tests.
- Full frontend: `npm.cmd test` - passed, 52 files / 187 tests.
- Build: `npm.cmd run build` - passed (`tsc --noEmit` and Vite production build); existing chunk-size warnings remain.
- Lint: `npm.cmd run lint` - passed with 0 errors and 14 pre-existing warnings outside changed Task 6 modules.
- Test stderr includes existing React Router future-flag, jsdom pseudo-element, and Ant Design deprecation warnings. The new modal tests also encounter the existing jsdom pseudo-element limitation.
- Diff hygiene: `git diff --check` is run as the final pre-commit gate.

## Residual Risk

- `createUser` and `updateUserAccess` are separate backend requests, so the browser cannot provide database-level atomic rollback. The UI reports partial success honestly and provides retry/recovery instead.
- A page reload after partial creation loses the modal retry state, but the created member remains selected in the roster when the flow fails and can be repaired through the normal resource-permissions tab.
- End-to-end confirmation against the authenticated legacy backend remains an environment-level follow-up.
