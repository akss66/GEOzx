# Identity Task 6 Report

## Scope

- Rebuilt the desktop member-governance workbench in `frontend/src/pages/Users.tsx` and focused modules under `frontend/src/components/users/`.
- Reworked `frontend/src/styles/user-workspace.css` for the approved warm-paper, dense desktop workspace.
- Added interaction coverage in `frontend/src/pages/Users.test.tsx` for the original Task 6 acceptance paths and the follow-up review findings.
- The review fix isolates member-owned security state, makes global access read-only, preserves unsaved access drafts across unrelated detail refetches, maps overview errors, and gates permanent deletion confirmation.
- Did not modify backend code, model-governance flows, or shared API contracts.

## Execution Boundary

- Entry point: `frontend/src/pages/Users.tsx`.
- Server state: React Query owns the member roster, per-member detail, access catalog, and current administrator secondary-password status.
- Local state: each member tab owns its form drafts and feedback. `MemberAccess` and `MemberSecurity` are keyed by member ID so member changes remount member-owned drafts; `PermanentDeletePanel` is also keyed by member ID inside the security subtree.
- Mutations: `createUser`, `updateUser`, `updateUserAccess`, `resetUserPassword`, `setSecondaryPassword`, `previewUserDeletion`, and `permanentlyDeleteUser` use the existing Task 5 API wrappers.
- Successful deletion updates the roster cache only after the API succeeds, removes the deleted detail query, and selects the adjacent member. This is not an optimistic deletion.

## TDD Evidence

- The follow-up review began by adding five regression cases for the five behavioral findings.
- RED: `npm.cmd test -- src/pages/Users.test.tsx` failed 5 of 13 tests. The failures directly showed cross-member security state reuse, editable global access, access-draft overwrite after refetch, unmapped overview save errors, and an enabled delete button without complete confirmation.
- The overview error case was then split into separate stable-code and generic-422 tests to avoid coupling two submissions to Ant Design's loading-icon transition.
- GREEN: the focused suite passed 14 of 14 tests after the production fixes.

Exact focused tests:

1. `auto-selects the first member and exposes the four governance tabs`
2. `filters the roster by search, role, anomaly, and status`
3. `saves identity changes and translates enable-disable business errors`
4. `toggles account scope, persists selected accounts, and explains when no accounts are visible`
5. `sets the current admin secondary password and resets the selected member password`
6. `recovers from stale delete previews and clears sensitive destructive inputs`
7. `clears destructive inputs when the delete flow closes`
8. `removes a permanently deleted member from the roster and selects the next member`
9. `isolates security drafts and deletion previews when the selected member changes`
10. `renders global access as read-only and blocks scoped access saves`
11. `preserves unsaved access drafts across overview save and status refetches`
12. `translates stable business errors from overview saves`
13. `translates generic 422 errors from overview saves`
14. `enables permanent deletion only after exact email and secondary password confirmation`

## UX And State Review

- The first visible member is selected automatically; roster search and filters remain local to the page.
- Global-access members see an explicit `全局访问（只读）` state and the effective account catalog, without customer/project/scope mutation controls or a save action.
- Scoped access drafts are initialized when the keyed member component mounts. Unrelated identity or activation refetches no longer replace a dirty draft; only member changes or successful access saves reset its local baseline.
- Permanent deletion remains a progressive preview-confirm flow. The final action is disabled until the preview allows deletion, the email matches exactly, and the secondary password is non-empty.

## Security Review

- Security/login drafts, deletion confirmation fields, feedback, preview data, and preview tokens cannot carry from member A to member B because the complete security subtree remounts on member ID changes.
- `preview_token`, email confirmation, current password, secondary password, and new login password remain component-local and are not placed in query keys, browser storage, URL state, or persistent caches.
- Permanent deletion clears email and secondary-password inputs on close, success, and every failure. Preview state is additionally cleared for stale, expired, invalid, or used preview codes; an ordinary failure keeps the non-sensitive preview visible for retry.
- Stable governance error codes are mapped to business copy. Generic HTTP 422 errors use the shared validation message; raw response bodies are not rendered.

## Accessibility Review

- Automated interaction tests verify named tabs, labeled inputs, roster selection state, disabled dangerous actions, status/alert text, and keyboard-focusable native or Ant Design controls.
- Dangerous and read-only states include explicit text and do not rely on color alone.
- A real-browser accessibility-tree, focus-order, visual, and screen-reader pass was not run because Chrome DevTools MCP is not configured in this session. No claim is made that automated jsdom checks replace that environment-level validation.

## Contract Notes

- There is no member-level audit endpoint in the current frontend contract, so the activity tab shows an honest unavailable state.
- The secondary-password contract does not expose attempts, so the UI reports that attempt count is unavailable.
- The member list contract does not expose aggregate lock state, so the top summary leaves the locked count unavailable.
- Global access is supplied by `UserDetail.has_global_access`; the frontend treats it as authoritative and blocks scoped access mutations.

## Verification

- Focused: `npm.cmd test -- src/pages/Users.test.tsx` - passed, 1 file / 14 tests.
- Full frontend: `npm.cmd test` - passed, 52 files / 181 tests.
- Build: `npm.cmd run build` - passed (`tsc --noEmit` and Vite production build); existing chunk-size warnings remain.
- Lint: `npm.cmd run lint` - exited successfully with 0 errors and 14 pre-existing warnings outside the changed Task 6 modules.
- Full-test stderr still contains existing React Router future-flag, jsdom pseudo-element, and Ant Design deprecation warnings outside the changed Users path.
- Browser runtime: not run; Chrome DevTools MCP is unavailable in this session.
- Diff hygiene: `git diff --check` is run as the final pre-commit gate.

## Residual Risk

- Member audit history, attempt counts, and aggregate member lock counts require backend contract additions before the UI can show real values.
- The API contract remains the integration authority for `has_global_access`; end-to-end verification against a running authenticated backend remains an environment-level follow-up.
