# Chat Composer and Login Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the oversized welcome composer with a compact ChatGPT-style auto-growing input and remove hardcoded demo credentials from the login page.

**Architecture:** Keep the existing `BrainComposer` and login submission flows intact. Express the composer behavior through Ant Design `Input.TextArea` autosizing plus scoped CSS, and lock both regressions down with component-level tests before running the full frontend and production deployment checks.

**Tech Stack:** React 18, TypeScript, Ant Design 5, Vitest, Testing Library, Vite, Docker Compose.

## Global Constraints

- Preserve Enter to send, Shift+Enter for newline, stop-generation, permission confirmation, authentication API, validation, and redirect behavior.
- The normal composer starts compact, grows from one through six rows, then scrolls internally.
- Keep the existing brand red and do not copy ChatGPT branding or proprietary assets.
- Do not restore the removed prompt shortcut chips.
- The login form must not contain demo email/password initial values or example credential placeholders.

---

### Task 1: Empty login form

**Files:**
- Create: `frontend/src/pages/Login.test.tsx`
- Modify: `frontend/src/pages/Login.tsx`

**Interfaces:**
- Consumes: the existing default `Login` component and `Form<LoginForm>` submission flow.
- Produces: a login form whose email and password values are empty before browser password-manager autofill.

- [ ] **Step 1: Write the failing test**

```tsx
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../stores/auth", () => ({
  useAuth: () => ({ token: null, setAuth: vi.fn() }),
}));

describe("Login", () => {
  afterEach(cleanup);

  it("does not prefill demo credentials", async () => {
    const { default: Login } = await import("./Login");
    render(<MemoryRouter><Login /></MemoryRouter>);

    expect(screen.getByLabelText("邮箱")).toHaveValue("");
    expect(screen.getByLabelText("密码")).toHaveValue("");
    expect(document.body).not.toHaveTextContent("admin@qq.com");
    expect(document.querySelector('input[value="admin123"]')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
pnpm test -- src/pages/Login.test.tsx
```

Expected: FAIL because the current form initializes `admin@qq.com` and `admin123`.

- [ ] **Step 3: Remove the demo values**

Delete this prop from the `Form` in `frontend/src/pages/Login.tsx`:

```tsx
initialValues={{ email: "admin@qq.com", password: "admin123" }}
```

Keep these browser-managed autocomplete attributes:

```tsx
autoComplete="email"
autoComplete="current-password"
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```powershell
pnpm test -- src/pages/Login.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit the login cleanup**

```powershell
git add -- frontend/src/pages/Login.tsx frontend/src/pages/Login.test.tsx
git commit -m "fix: remove demo login credentials"
```

### Task 2: ChatGPT-style adaptive composer

**Files:**
- Modify: `frontend/src/components/brain/BrainComposer.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.test.tsx`
- Modify: `frontend/src/styles/brain-v2.css`

**Interfaces:**
- Consumes: `BrainComposer` props and callbacks without changing their signatures.
- Produces: `Input.TextArea` configured with `autoSize={{ minRows: 1, maxRows: 6 }}` and a compact floating composer layout.

- [ ] **Step 1: Write the failing component test**

Add to `BrainComposer.test.tsx`:

```tsx
it("starts compact and grows through six rows", () => {
  render(
    <BrainComposer
      value=""
      disabled={false}
      loading={false}
      pendingPermission={null}
      approvalComment=""
      approving={false}
      onChange={vi.fn()}
      onApprovalCommentChange={vi.fn()}
      onApprovePermission={vi.fn()}
      onSubmit={vi.fn()}
    />,
  );

  const input = screen.getByPlaceholderText(
    "输入目标、补充要求、打断指令，或直接问一个问题。",
  );
  expect(input).toHaveAttribute("data-autosize-min-rows", "1");
  expect(input).toHaveAttribute("data-autosize-max-rows", "6");
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
pnpm test -- src/components/brain/BrainComposer.test.tsx
```

Expected: FAIL because the current input is fixed to three rows and has no autosize contract.

- [ ] **Step 3: Implement autosizing without changing callbacks**

Replace `rows={3}` with:

```tsx
autoSize={{ minRows: 1, maxRows: 6 }}
data-autosize-min-rows="1"
data-autosize-max-rows="6"
```

Disable the send action for empty trimmed input while retaining all existing `disabled` and `loading` checks:

```tsx
disabled={disabled || value.trim().length === 0}
```

- [ ] **Step 4: Restyle the ordinary message composer**

In `frontend/src/styles/brain-v2.css`, update only the normal message-mode selectors:

```css
.tz-brain-thread .dy-brain-composer-box:not([data-mode="permission"]) {
  position: relative;
  min-height: 64px;
  padding: 11px 60px 11px 18px;
  border-radius: 32px;
  box-shadow: 0 8px 24px rgba(42, 35, 27, 0.07);
}

.tz-brain-thread .dy-brain-composer-box .dy-brain-input.ant-input-textarea-affix-wrapper,
.tz-brain-thread .dy-brain-composer-box .dy-brain-input textarea.ant-input {
  min-height: 24px;
}

.tz-brain-thread .dy-brain-composer-box .dy-brain-input textarea.ant-input {
  max-height: 144px;
  padding: 5px 0;
  line-height: 24px;
  overflow-y: auto !important;
}

.tz-brain-thread .dy-brain-composer-tools {
  position: absolute;
  right: 12px;
  bottom: 13px;
  padding: 0;
}

.tz-brain-thread .dy-brain-composer-tools > .ant-btn {
  width: 36px;
  min-width: 36px;
  height: 36px;
  min-height: 36px;
  border-radius: 50%;
}
```

Retain the existing permission-mode layout and button group by scoping floating styles to `:not([data-mode="permission"])`.

- [ ] **Step 5: Run the focused composer tests**

Run:

```powershell
pnpm test -- src/components/brain/BrainComposer.test.tsx src/pages/BrainHome.test.tsx
```

Expected: PASS with the existing Enter, stop, permission, and no-shortcut regressions intact.

- [ ] **Step 6: Commit the composer change**

```powershell
git add -- frontend/src/components/brain/BrainComposer.tsx frontend/src/components/brain/BrainComposer.test.tsx frontend/src/styles/brain-v2.css
git commit -m "style: compact operations brain composer"
```

### Task 3: Full verification and production release

**Files:**
- Verify: `frontend/`
- Deploy: repository archive at the final commit

**Interfaces:**
- Consumes: Tasks 1 and 2 commits.
- Produces: a healthy production deployment at `https://tzxai.top`.

- [ ] **Step 1: Run the complete frontend suite**

```powershell
pnpm test
pnpm build
```

Expected: all Vitest tests pass and `tsc --noEmit && vite build` exits 0.

- [ ] **Step 2: Browser-check the local or production build**

Verify:

- Login email and password inputs are empty when browser credential autofill is disabled.
- The welcome composer is compact at zero content and grows for multiline content.
- Enter sends, Shift+Enter inserts a newline, and the send button remains visible.
- The four removed prompt shortcuts remain absent.
- No new console errors appear.

- [ ] **Step 3: Package the exact final commit**

```powershell
$commit = (git rev-parse --short HEAD).Trim()
git archive --format=tar.gz -o "C:\tmp\dyflow-20260728-composer-login-$commit.tar.gz" HEAD
Get-FileHash -Algorithm SHA256 "C:\tmp\dyflow-20260728-composer-login-$commit.tar.gz"
```

Check that the archive includes `frontend/src/pages/Login.tsx`, `frontend/src/components/brain/BrainComposer.tsx`, `frontend/src/styles/brain-v2.css`, and `docker-compose.prod.yml`, and contains no real `.env`.

- [ ] **Step 4: Build and deploy through the existing release-directory workflow**

Upload the archive to `/home/admin/releases/`, verify its SHA-256 on the server, extract to a new immutable release directory, copy the current production `.env`, build the Compose images, run `alembic upgrade head`, atomically switch `/home/admin/dyflow`, and run:

```bash
docker compose -f docker-compose.prod.yml up -d --remove-orphans
```

Keep `/home/admin/releases/dyflow-20260728-brand-cleanup-4137a24` as the rollback target.

- [ ] **Step 5: Verify production health and UI**

```powershell
curl.exe -fsS https://tzxai.top/api/health/ready
curl.exe -fsS -o NUL -w "root_status=%{http_code} tls_verify=%{ssl_verify_result}`n" https://tzxai.top/
```

Expected:

- `{"status":"ready","checks":{"db":true,"redis":true}}`
- root status `200`
- TLS verification result `0`
- all Compose services healthy or running
- migration `20260727_0300 (head)`
- production browser matches the two acceptance checks with no new console errors.
