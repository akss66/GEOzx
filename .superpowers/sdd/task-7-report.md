# Identity Governance Task 7 Verification Report

- Date: 2026-07-20 (Asia/Shanghai)
- Workspace: `C:\Users\AKSSINA\Desktop\Workplace\GEOZX`
- Branch: `workspace-freeze-20260706`
- Verified HEAD: `32f6f90` (`fix: guard legacy member access details`)
- Scope: non-browser verification only; no production services or existing real users were touched.
- Initial status: **BLOCKED** (superseded by the final acceptance below)

## Decision

Task 7 is not accepted. The backend Ruff gate failed, and the planned frontend
`npm audit` gate could not execute because the repository has only
`pnpm-lock.yaml`. A supplemental full pnpm audit also reported one high and one
critical development-tool vulnerability. Per the task brief, `tasks/current.md`
was not updated and no acceptance commit was created.

## Planned Quality Gates

| Gate | Command | Exit | Result |
| --- | --- | ---: | --- |
| Backend tests | `cd backend && python -m pytest -q` | 0 | PASS: 263 passed in 118.81s; 323 warnings |
| Backend lint | `cd backend && python -m ruff check app tests` | 1 | FAIL: 4 violations |
| Frontend tests | `cd frontend && npm.cmd test` | 0 | PASS: 52 files, 190 tests |
| Frontend build | `cd frontend && npm.cmd run build` | 0 | PASS: TypeScript and Vite build completed |
| Frontend audit | `cd frontend && npm.cmd audit --audit-level=high` | 1 | FAIL: `ENOLOCK`; no npm lockfile exists |
| Secret persistence grep | `git grep -n -E "(secondary_password\|api_key).*(console\|logger\|localStorage\|sessionStorage)" -- backend frontend` | 1 (native no-match) | PASS: no matches; wrapper normalized the expected no-match result to exit 0 |

### Ruff Failures

1. `backend/app/api/feedback.py:3`: `I001` unsorted import block.
2. `backend/tests/test_approval_workspace_api.py:168`: `E501`, 102 characters.
3. `backend/tests/test_workspace_access.py:1`: `I001` unsorted import block.
4. `backend/tests/test_workspace_access.py:10`: `E501`, 103 characters.

The affected lines are tracked and committed, not local uncommitted edits. Git
blame attributes the identity-access additions to commits `8a644eb` and
`e7607dc`. No source or test file was changed to make the gate pass.

### Dependency Audit Investigation

- Repository lockfile: `frontend/pnpm-lock.yaml`; no `package-lock.json` or
  `npm-shrinkwrap.json` is present. This is why the planned npm command returns
  `ENOLOCK` before auditing dependencies.
- Supplemental `pnpm.cmd audit --audit-level=high`: exit 1, five findings total
  (3 moderate, 1 high, 1 critical).
- Critical: `vitest <3.2.6`, GHSA-5xrq-8626-4rwp.
- High: nested Vite versions `<=6.4.2` under Vitest, GHSA-fx2h-pf6j-xcff.
- Supplemental `pnpm.cmd audit --prod --audit-level=high`: exit 0, no known
  production dependency vulnerabilities.

The production-only result is useful context, but it does not make the planned
audit command pass and does not clear the development-tool findings.

## Identity-Governance Integration Evidence

Focused command:

`cd backend && python -m pytest tests/test_admin_secondary_password.py tests/test_user_deletion_api.py tests/test_users_management_api.py -q`

Result: exit 0, 47 passed in 37.77s, 77 warnings.

Existing tests verified:

- Current-password validation before setting the secondary password, secret
  redaction, 10-minute set/reset cooldown, fifth-failure lockout, concurrency,
  and reset of the failure state after successful verification.
- Wrong target email rejected without consuming a password attempt; wrong
  secondary password rejected while retaining the target and owned assets.
- Stale, expired, actor-mismatched, and reused deletion previews rejected.
- Permanent deletion transaction rollback, owned-root deletion, linked-event
  cleanup, sanitized receipt, self-deletion protection, and last-active-admin
  protection.
- Member profile edit/deactivation, password reset without plaintext response or
  event leakage, and atomic client/project/account authorization replacement.
- Full backend suite also includes `test_admin_creates_user`.

No live database fixture, online API, production environment, or existing real
user was used. All destructive-flow evidence came from isolated pytest fixtures
and test transactions.

## Non-Browser Coverage And Gaps

- Frontend automated tests cover mocked member creation, profile/access flows,
  secondary-password setup, reset-password interaction, stale-preview recovery,
  destructive-input clearing, and removal of a permanently deleted member.
- No browser or manual desktop acceptance was performed because this assignment
  is explicitly non-browser verification. Therefore no claim is made about real
  desktop rendering, focus behavior, or end-to-end frontend/backend wiring.
- Existing backend tests explicitly exercise deactivation but do not name a
  dedicated deactivate-then-reactivate lifecycle test. Re-enable remains a
  coverage gap for this non-browser acceptance pass.
- The planned secret grep is line-oriented and pattern-order-sensitive. It found
  no prohibited match, but it is not a general taint analysis.

## Other Warnings And Risks

- Backend tests emitted 323 warnings. Most are PyJWT warnings that the test HMAC
  key is 30 bytes rather than the recommended minimum 32 bytes; one warning is a
  Starlette TestClient/httpx deprecation.
- Frontend tests emit React Router v7 future-flag notices, jsdom
  `getComputedStyle` pseudo-element limitations, and one Ant Design deprecation.
- The build succeeded but reported `vendor-antd` and `vendor-charts` chunks over
  500 kB after minification.
- Because required gates failed, release readiness is blocked even though all
  backend/frontend tests and the production build passed.

## Repository Actions

- `tasks/current.md`: not modified.
- Business code and tests: not modified.
- Production deployment: not attempted.

---

## Final Task 7 Acceptance

- Date: 2026-07-20 (Asia/Shanghai)
- Status: **PASS**
- Production deployment: not attempted.

### Final Quality Gates

| Gate | Result |
| --- | --- |
| Backend full suite | 263 passed, 323 existing warnings |
| Backend Ruff | All checks passed |
| Frontend full suite | 53 files, 194 tests passed |
| Frontend production build | Passed with existing large-chunk warnings |
| Frontend ESLint | 0 errors, 14 pre-existing warnings |
| Full dependency audit | 0 known vulnerabilities |
| Production dependency audit | 0 known vulnerabilities |
| Sensitive logging/storage grep | No matches |

### Desktop Acceptance

- The real local environment verified create, profile edit, disable/enable,
  customer/project/account authorization, login-password reset, and audit-aware
  cleanup. The disposable real-local member was disabled through the application
  service after acceptance.
- A separate SQLite database and separate backend/frontend ports were used for
  destructive acceptance. The administrator secondary password was configured,
  its cooldown was overridden only inside that disposable database, and the
  permanent-deletion impact preview was inspected.
- A wrong secondary password produced the localized business error while the
  session and deletion preview remained available. Retrying with the correct
  secondary password permanently deleted the target member and reduced the
  roster from two members to one.
- The isolated browser session, frontend/backend processes, and temporary
  database were all closed and removed after acceptance.
- Acceptance documentation is included in the final Task 7 commit.

---

## Frontend Dependency Audit Remediation

- Date: 2026-07-20 (Asia/Shanghai)
- Scope: `frontend/package.json` and `frontend/pnpm-lock.yaml`; this report was
  appended as requested. No application source, backend file, or test was
  changed by this remediation.
- Status: **PASS**

### Root Cause Reproduction

Before the upgrade, `pnpm.cmd audit --audit-level=high` exited 1 with five
findings (3 moderate, 1 high, 1 critical):

- `vitest <3.2.6`, GHSA-5xrq-8626-4rwp (critical).
- Vitest's nested Vite path, GHSA-fx2h-pf6j-xcff (high).

The lockfile resolved the declared `vitest ^2.1.8` to 2.1.9 and its nested
Vite to 5.4.21, while the direct Vite dependency already resolved to 6.4.3.

### Minimal Upgrade

| Dependency | Manifest before | Manifest after | Resolved after |
| --- | --- | --- | --- |
| `vitest` | `^2.1.8` | `^3.2.6` | `3.2.6` |
| `vite` | `^6.0.3` | `^6.4.3` | `6.4.3` |

Vitest, `@vitest/mocker`, and `vite-node` now share the safe Vite 6.4.3
resolution; the obsolete nested Vite 5/esbuild 0.21 subtree was removed.

### Verification

| Gate | Exit | Result |
| --- | ---: | --- |
| `pnpm.cmd audit --audit-level=high` | 0 | PASS: no known vulnerabilities |
| `pnpm.cmd audit --prod --audit-level=high` | 0 | PASS: no known vulnerabilities |
| `npm.cmd test` | 0 | PASS: 52 files, 190 tests |
| `npm.cmd run build` | 0 | PASS: TypeScript check and Vite 6.4.3 build |

Tests retained the existing React Router future-flag, jsdom pseudo-element,
and Ant Design deprecation warnings. The build retained the existing warning
for chunks over 500 kB. These warnings did not fail a gate and were not
expanded into unrelated source changes.

### Repository Actions

- Commit: `45e4822` (`chore: update frontend test toolchain security`).
- Production deployment: not attempted.

## Ruff Gate Remediation

- Date: 2026-07-20 (Asia/Shanghai)
- Scope: formatting-only changes in the three assigned Python files.
- Initial reproduction: `cd backend && python -m ruff check app tests`
  exited 1 with exactly four violations: two `I001` import-order failures and
  two `E501` line-length failures.
- Fix: sorted the affected imports and wrapped the two overlong declarations.
  No names, calls, assertions, fixtures, or runtime behavior were changed.
- Ruff verification: `cd backend && python -m ruff check app tests` exited 0
  with `All checks passed!`.
- Focused tests: `cd backend && python -m pytest
  tests/test_approval_workspace_api.py tests/test_workspace_access.py
  tests/test_optimization_feedback.py tests/test_review_workspace_api.py -q`
  exited 0 with 20 passed and 25 warnings in 10.85s on the final post-commit run.
- The warnings are the existing PyJWT test-key length warnings.
- Commit: `7ac225f` (`style: satisfy identity governance lint gate`).
- Production deployment: not attempted.

---

## Step-Up Authentication Session Preservation Fix

- Date: 2026-07-20 (Asia/Shanghai)
- Starting HEAD: `45e4822` (`chore: update frontend test toolchain security`)
- Scope: Task 7 desktop-acceptance regression in secondary-password setup and
  permanent deletion. No deployment was attempted.
- Status: **PASS**

### Root Cause

`frontend/src/api/client.ts` removed `dyflow_token` for every HTTP 401. The
permanent-delete API already returned wrong secondary passwords as a structured
business 401 (`SECONDARY_PASSWORD_INVALID`), but the interceptor treated that
step-up-authentication failure as an expired login. The secondary-password setup
API returned its wrong-current-password 401 as an unstructured string, so it
could not be distinguished from a session-authentication failure.

### RED Evidence

Tests were changed before production code and run against the original behavior:

| Command | Exit | Expected failure observed |
| --- | ---: | --- |
| `cd frontend && npm.cmd test -- src/api/client.test.ts` | 1 | 1 failed, 1 passed; structured `SECONDARY_PASSWORD_INVALID` removed the token (`expected "active-session", received null`) while the ordinary 401 control passed. |
| `cd backend && python -m pytest tests/test_admin_secondary_password.py::test_secondary_password_requires_current_password -q` | 1 | Expected structured `CURRENT_PASSWORD_INVALID`; received the original `"Invalid current password"` string. |
| `cd frontend && npm.cmd test -- src/pages/Users.test.tsx` | 1 | The explicit current-password copy was absent because `CURRENT_PASSWORD_INVALID` had no frontend governance mapping. |

The new deletion-flow test also asserted that `SECONDARY_PASSWORD_INVALID`
keeps the existing preview visible and permits a second delete submission
without another preview request. Its first run exposed a test-only Ant Design
loading-name timing issue; the query was adjusted to accept the button's loading
accessible name without changing production behavior.

### Minimal Fix

- The Axios response interceptor now clears the token only for an HTTP 401 that
  does not contain a string `detail.code`. Structured business 401 responses are
  rejected to their caller with the token intact; ordinary authentication 401
  responses retain the existing logout behavior.
- Wrong current passwords now return HTTP 401 with
  `detail.code=CURRENT_PASSWORD_INVALID` and a stable message.
- The frontend governance error union and copy map now display
  `当前登录密码不正确，请重新输入。`.
- The existing permanent-delete panel already retained previews for all errors
  outside its four preview-reset codes, so no component implementation change
  was required.

### GREEN And Regression Evidence

| Gate | Exit | Result |
| --- | ---: | --- |
| Frontend interceptor focused test | 0 | 1 file, 2 tests passed. |
| Frontend Users focused test | 0 | 1 file, 23 tests passed, including explicit current-password copy and retry with one preview request. |
| Backend focused contract test | 0 | 1 passed, 2 existing PyJWT key-length warnings. |
| Backend related governance suites | 0 | 47 passed, 77 existing PyJWT key-length warnings in 41.49s. |
| Backend focused Ruff | 0 | `All checks passed!` for the two modified Python files. |
| Frontend full test suite | 0 | 53 files, 194 tests passed in 21.74s. |
| Frontend build | 0 | TypeScript and Vite build passed; existing over-500-kB chunk warnings remain. |
| Frontend lint | 0 | 0 errors; 14 pre-existing warnings. Final `eslint . --quiet` also exited 0. |
| `git diff --check` | 0 | No whitespace errors. |

The frontend full suite retained existing React Router future-flag, jsdom
pseudo-element, and Ant Design deprecation warnings. No unrelated warning was
addressed in this fix.

### Repository Scope

- Modified only the allowed backend/frontend implementation and test files plus
  this report.
- The pre-existing untracked `backend/.acceptance/` directory was not modified,
  staged, or committed.
- Production deployment: not attempted.
