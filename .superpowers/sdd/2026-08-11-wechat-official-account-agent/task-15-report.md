# Task 15 Report

## Status

DONE_WITH_CONCERNS

## TDD evidence

- Service RED: `wechatIntegration.test.ts` failed collection because `./wechatIntegration` did not exist.
- Service GREEN: 4 tests cover authorization allowlisting, official HTTPS host validation, capability allowlisting, paginated bases, and binding endpoints.
- Accounts RED: two tests could not find the WeChat authorization/capability actions.
- Accounts GREEN: 8 tests pass, including exact official URL opening, secret non-rendering, capability reasons, fixed first-release freepublish label, and account-key isolation.
- Knowledge RED: two tests could not find primary-brand binding and named replacement actions.
- Knowledge GREEN: 9 tests pass, including first bind, shared-base exclusion, named confirmation, cancel-without-PUT, account-key isolation, loading/empty/error behavior.

## Changed files

- Added `frontend/src/types/wechatArticle.ts`.
- Added `frontend/src/services/wechatIntegration.ts` and its tests.
- Updated Accounts and Knowledge pages/tests.
- Added the platform union member and the minimum exhaustive UI label/color mappings.
- `PublishPreparation.tsx` has a one-line type compatibility narrowing because the existing publish contract still accepts only the original three platforms; behavior is unchanged.

## UI, accessibility, and security review

- Reused the existing warm-paper/ink design system; no CSS files were changed.
- Actions are native buttons with accessible names. Replacement uses keyboard-operable protected focus via Popconfirm and names both bases.
- Loading, empty, error, retry, and live status copy are present. Errors do not render backend details.
- React Query capability and binding keys include account ID. Organization-shared bases are filtered from primary choices.
- Service responses are parsed through explicit allowlists; unknown/token/raw-state fields do not survive in frontend objects.
- Authorization opens only `https://mp.weixin.qq.com/cgi-bin/componentloginpage` using `_blank` with `noopener,noreferrer`.
- `freepublish` always renders `首版未开启`.

## Verification

- Focused Vitest: 21/21 passed.
- ESLint: passed.
- TypeScript + Vite production build: passed (existing large-chunk warning only).
- `git diff --check`: passed.
- Impeccable detector: `[]` on changed UI targets.
- Secret scan: passed; occurrences are test fixtures, allowlist rejection checks, or existing input labels only.

## Concerns

- Automated Chromium smoke setup was attempted three times but the local mocked auth/application shell never rendered the account fixture, so browser screenshots/real 390px runtime evidence could not be completed within the task timebox. Component tests cover the responsive-safe semantic states, but visual browser verification remains outstanding.
- Build retains the repository's pre-existing large-chunk warning.

## Commit

Atomic commit: `feat: authorize and bind WeChat accounts`. The final handoff SHA is authoritative because a commit cannot embed its own final hash without changing that hash.

## Review fix round 1

### Findings resolved

- Knowledge-base discovery now follows backend pagination until `total`. Empty/non-advancing pages fail explicitly, and a 100-page ceiling prevents unbounded requests. Tests prove a brand on page 2 is returned and selectable while `organization_shared` remains excluded.
- The WeChat authorization-session endpoint's real `422` setup failure now gives the concrete recovery action: configure the WeChat third-party platform AppID and authorization callback URL. Raw backend detail is never rendered.
- WeChat workspaces no longer expose the legacy “发布准备” entry. `PublishPreparation` also has a runtime guard that renders an unsupported state without mounting queries or calling legacy publish APIs. The previous platform cast was removed.

### TDD and verification

- RED: pagination made only one request; the 422 path rendered generic copy; WeChat mounted the legacy publish form; ContentCanvas still exposed “发布准备”.
- GREEN: 37/37 focused and adjacent tests passed across service, Accounts, Knowledge, ContentCanvas, PublishPreparation, ContentWorkspace, and PublishJobPanel.
- ESLint passed.
- TypeScript and Vite production build passed; only the repository's existing large-chunk warning remains.
- Impeccable detector returned `[]` for the changed production UI targets.
- Browser smoke was not retried in this review round, per the bounded follow-up instruction; the original report's browser concern remains.
