# Identity Task 6 Report

## Scope

- Rebuilt the desktop member-governance workbench in `frontend/src/pages/Users.tsx` and focused modules under `frontend/src/components/users/`.
- Reworked `frontend/src/styles/user-workspace.css` for the approved warm-paper, dense desktop workspace.
- Added interaction coverage in `frontend/src/pages/Users.test.tsx` for the original Task 6 acceptance paths and review findings.
- Added a discriminated wire-response compatibility type and normalization in `frontend/src/types.ts` and `frontend/src/api/auth.ts` because an authenticated live backend returned an access catalog without `accounts`.
- Added an authenticated-shell Playwright regression for the 1440 x 900 desktop layout.
- Did not modify backend code or model-governance flows.

## Execution Boundary

- Entry point: `frontend/src/pages/Users.tsx`.
- Server state: React Query owns the member roster, per-member detail, normalized access catalog, and current administrator secondary-password status.
- API boundary: `getUserAccessCatalog` normalizes missing `clients` and `projects` to empty collections, but an omitted/non-array `accounts` collection becomes `account_catalog_status: "unavailable"`. A present array, including a legitimate empty array, becomes `"available"`. Missing per-account `project_ids` is normalized to an empty array only when the account catalog is available.
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

- Earlier Task 6 and review work remains covered by the focused suite below.
- This final review RED: `npm.cmd test -- src/pages/Users.test.tsx src/api/auth.test.ts` failed 3 of 29 tests before production changes. The failures proved the API fabricated `accounts: []`, global access showed a false empty state, and a scoped member's existing `[101]` whitelist was submitted as `[]` after an unrelated role edit.
- Desktop RED: after correcting the Playwright fixture so it intercepted only `/api/*`, Chromium measured `documentScrollWidth=1524` at a 1440px viewport and failed the overflow assertion.
- GREEN: the two-file run passed 29 of 29, focused Users passed 20 of 20, and the 1440 x 900 Chromium regression passed.

Exact `Users.test.tsx` tests:

1. `auto-selects the first member and exposes the four governance tabs`
2. `renders an honest unavailable state when the legacy catalog omits accounts`
3. `preserves an existing selected-account whitelist when the account catalog is unavailable`
4. `creates a scoped member with initial resource authorization in one flow`
5. `recovers from a partial create result without creating the identity twice`
6. `creates an administrator without requiring scoped grants`
7. `filters the roster by search, role, anomaly, and status`
8. `saves identity changes and translates enable-disable business errors`
9. `toggles account scope, persists selected accounts, and explains when no accounts are visible`
10. `sets the current admin secondary password and resets the selected member password`
11. `recovers from stale delete previews and clears sensitive destructive inputs`
12. `clears destructive inputs when the delete flow closes`
13. `removes a permanently deleted member from the roster and selects the next member`
14. `isolates security drafts and deletion previews when the selected member changes`
15. `renders global access as read-only and blocks scoped access saves`
16. `preserves unsaved access drafts across overview save and status refetches`
17. `drops an unsaved access draft when the selected member changes`
18. `translates stable business errors from overview saves`
19. `translates generic 422 errors from overview saves`
20. `enables permanent deletion only after exact email and secondary password confirmation`

The targeted API regression is `preserves an unavailable account catalog when legacy responses omit accounts` in `frontend/src/api/auth.test.ts`. The layout regression is `member governance fits the 1440px desktop viewport without document overflow` in `frontend/e2e/users-layout.spec.ts`.

## UX And Accessibility Review

- The first visible member is selected automatically; roster search and filters remain local to the page.
- Global-access members see explicit read-only copy and an unavailable state when legacy responses omit accounts; scoped mutation controls are absent.
- Scoped members retain their saved account mode and IDs when the account catalog is unavailable. Account-scope radios are disabled, account counts are not invented, and client/project edits preserve the account fields verbatim on save.
- A present empty account array renders a true empty state. An omitted account collection renders a compatibility-unavailable state. No placeholder clients, projects, or accounts are created.
- The member workspace now shrinks inside the fixed desktop shell instead of forcing a 1240px content minimum; the header action and inspector remain inside a 1440px viewport.
- Initial authorization uses labeled fieldsets, checkboxes, role selects, and named account-scope radios. The partial-result message uses an alert role and retry remains keyboard-operable.
- Dangerous and read-only states include explicit text and do not rely on color alone.
- A deterministic Chromium pass bootstraps the authenticated shell through `/auth/me` mocks and checks document/body width plus the header action and inspector bounds. It is a layout regression, not a substitute for a full live-backend accessibility audit.

## Security Review

- Security/login drafts, deletion confirmation fields, feedback, preview data, and preview tokens cannot carry from member A to member B because the complete security subtree remounts on member ID changes.
- `preview_token`, email confirmation, current password, secondary password, and new login password remain component-local and are not placed in query keys, browser storage, URL state, or persistent caches.
- The create flow clears the initial login password immediately after identity creation and on modal close/success. A partial access failure never resubmits `createUser`.
- Permanent deletion clears email and secondary-password inputs on close, success, and every failure. Stale, expired, invalid, or used previews also clear preview state.
- Stable governance error codes map to business copy; raw response bodies are not rendered.

## Contract Notes

- Current repository backend code declares `clients`, `projects`, and `accounts` as required in `UserAccessCatalogOut` and returns all three from `get_access_catalog`.
- Browser acceptance showed a deployed or legacy backend response without `accounts`. The frontend now preserves that distinction at the API boundary with an explicit unavailable variant.
- An available empty array means there are genuinely no catalog accounts. An omitted/non-array collection means compatibility is unavailable; the UI does not infer emptiness and will not clamp or erase existing `account_ids`.
- There is no member-level audit endpoint in the current frontend contract, so the activity tab shows an honest unavailable state.
- The secondary-password contract does not expose attempts, and the member list does not expose aggregate lock state; those values remain unavailable rather than invented.

## Verification

- Focused Users: `npm.cmd test -- src/pages/Users.test.tsx` - passed independently, 1 file / 20 tests.
- Targeted Users + API: `npm.cmd test -- src/pages/Users.test.tsx src/api/auth.test.ts` - passed, 2 files / 29 tests.
- Desktop Chromium: `npx.cmd playwright test e2e/users-layout.spec.ts --project=chromium` - passed, 1 test at 1440 x 900.
- Full frontend: `npm.cmd test` - passed, 52 files / 188 tests.
- Build: `npm.cmd run build` - passed (`tsc --noEmit` and Vite production build); existing chunk-size warnings remain.
- Lint: `npm.cmd run lint` - passed with 0 errors and 14 pre-existing warnings outside changed Task 6 modules.
- Test stderr includes existing React Router future-flag, jsdom pseudo-element, and Ant Design deprecation warnings. The new modal tests also encounter the existing jsdom pseudo-element limitation.
- Diff hygiene: `git diff --check` is run as the final pre-commit gate.

## Residual Risk

- `createUser` and `updateUserAccess` are separate backend requests, so the browser cannot provide database-level atomic rollback. The UI reports partial success honestly and provides retry/recovery instead.
- A page reload after partial creation loses the modal retry state, but the created member remains selected in the roster when the flow fails and can be repaired through the normal resource-permissions tab.
- The Chromium regression uses deterministic API mocks. Reconfirmation against the deployed legacy backend remains an environment-level follow-up.
