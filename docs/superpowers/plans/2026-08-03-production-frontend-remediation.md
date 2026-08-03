# Production Frontend Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the production-readiness UI, interaction, performance, accessibility, and frontend data-loading issues found in the 2026-08-03 audit, while explicitly deferring mobile-only layout work.

**Architecture:** Keep the existing React/Ant Design shell and API boundaries. Add small reusable interaction helpers, lazy-load page routes, expose roster anomaly summaries from the existing users API, and guard local editor drafts at the points where they can be discarded. Preserve the current visual direction while consolidating the active light-theme tokens.

**Tech Stack:** React 18, TypeScript, React Router 6, TanStack Query, Ant Design, Vitest, Playwright, FastAPI, SQLAlchemy, pytest.

## Global Constraints

- Mobile-only layout remediation is deferred for this release.
- Do not expose placeholder customer-service or advertising pages in production routes.
- Preserve existing API authorization and tenant boundaries.
- Every changed behavior receives a regression test before implementation.
- Do not deploy or push as part of this plan unless separately requested.

---

### Task 1: Route performance and canonical information architecture

**Files:** `frontend/src/App.tsx`, `frontend/src/appRoutes.ts`, `frontend/src/appRoutes.test.ts`, `frontend/src/components/shell/navigation.tsx`, `frontend/src/components/AppShell.tsx`, `frontend/src/components/AppShell.test.tsx`.

**Produces:** `selectedNavigationKey(pathname: string): string`, lazy page chunks, and a canonical `/pipeline` to `/tasks` redirect.

- [ ] Add failing tests asserting risks appears in navigation, placeholder routes are absent, `/pipeline` is not an app page, and account-data paths select `/accounts`.
- [ ] Run the focused Vitest tests and confirm the new assertions fail.
- [ ] Replace eager page imports with `React.lazy`, add a route-level loading fallback, remove placeholder pages, and add the canonical redirect.
- [ ] Add the risk queue to navigation and normalize nested/legacy selected keys.
- [ ] Re-run the focused tests and `pnpm build`.

### Task 2: Shell overlay and help interactions

**Files:** create `frontend/src/hooks/useDismissibleLayer.ts` and its test; modify `WorkspaceSwitcher`, `NotificationCenter`, `GlobalSearch`, `AppShell`, their tests, and `frontend/src/styles/app-shell.css`.

**Produces:** `useDismissibleLayer({ open, onDismiss, panelRef, triggerRef })`.

- [ ] Add failing tests for Escape/outside-click dismissal and trigger focus restoration.
- [ ] Run the focused shell tests and confirm the new assertions fail.
- [ ] Implement the shared hook and adopt it in workspace and notification panels.
- [ ] Use Ant Design Modal focus management for global search and add a functional help drawer.
- [ ] Re-run focused tests and keyboard-check the shell in Playwright.

### Task 3: User roster summary without N+1 requests

**Files:** `backend/app/schemas/auth.py`, `backend/app/api/users.py`, `backend/tests/test_auth_api.py`, `frontend/src/types.ts`, `frontend/src/api/auth.ts`, `frontend/src/pages/Users.tsx`, `frontend/src/pages/Users.test.tsx`.

**Produces:** `GET /users` roster rows with `access_anomaly: boolean`; frontend `UserRosterItem extends User`.

- [ ] Add backend assertions that unassigned members are anomalous and admins are not.
- [ ] Add a frontend test asserting the page requests only the selected member detail and filters using roster summaries.
- [ ] Run focused pytest/Vitest tests and confirm failure.
- [ ] Build summaries with SQL `exists` predicates and update the frontend to one selected-detail query.
- [ ] Re-run focused tests and verify the initial page no longer requests every detail.

### Task 4: Unsaved editor protection

**Files:** `frontend/src/pages/Config.tsx`, `frontend/src/pages/Config.test.tsx`, `frontend/src/pages/Users.tsx`, `frontend/src/pages/Users.test.tsx`, `frontend/src/components/users/MemberAccess.tsx`, `frontend/src/components/users/MemberAccess.test.tsx`.

**Produces:** `onDirtyChange?: (dirty: boolean) => void` on `MemberAccess`.

- [ ] Replace the existing draft-loss expectation with failing confirmation/cancel/continue tests.
- [ ] Add a failing Config test proving an edited expert is not switched without confirmation.
- [ ] Run focused tests and confirm failure.
- [ ] Report dirty state upward, confirm before member/expert changes, and register `beforeunload` while dirty.
- [ ] Re-run focused tests.

### Task 5: Conversation, login, and import accessibility polish

**Files:** `frontend/src/pages/BrainHome.tsx`, `frontend/src/pages/BrainHome.test.tsx`, `frontend/src/pages/Login.tsx`, `frontend/src/pages/Login.test.tsx`, `frontend/src/components/account-data/BulkImportQueue.tsx`, its test, `frontend/e2e/account-data-import.spec.ts`, and `frontend/src/styles/brain-v2.css`.

**Produces:** a visible “回到最新消息” action only when the user intentionally leaves the live tail.

- [ ] Add failing tests for jump-to-latest visibility, absence of unavailable social-login controls, and one native file-picker button.
- [ ] Run focused tests and confirm failure.
- [ ] Implement the jump action, hide unavailable login methods, and remove nested interactive semantics from the dropzone.
- [ ] Update stale E2E selectors to the production accessible name.
- [ ] Re-run focused tests and relevant Playwright specs.

### Task 6: Active design tokens and final quality gates

**Files:** `frontend/src/styles/foundation.css`, `frontend/src/index.css`, `frontend/src/styles/high-fidelity-system.css`, plus touched files with lint findings only.

**Produces:** one active light-theme token source with legacy aliases mapped to it.

- [ ] Add a token regression assertion for accessible secondary text and removal of the active dark override.
- [ ] Consolidate live aliases into `foundation.css`, darken faint text to WCAG AA, and retain compatibility aliases for unmigrated components.
- [ ] Run `pnpm lint`, `pnpm test`, and `pnpm build` from `frontend`.
- [ ] Run focused backend tests and safe mocked Playwright suites.
- [ ] Review `git diff --check`, `git status --short`, and every requirement before reporting completion.
