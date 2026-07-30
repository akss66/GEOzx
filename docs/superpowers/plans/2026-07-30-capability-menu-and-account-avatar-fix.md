# Capability Menu and Account Avatar Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the operations-brain capability menu fully visible inside the usable page area and show the synchronized Douyin avatar in the selected-account trigger.

**Architecture:** Render the capability menu through a React Portal so it is no longer clipped by `.tz-brain-stage`, and calculate a fixed viewport position from the trigger and stage bounds. Reuse the existing `AccountAvatar` component in the account trigger; the backend and account API remain unchanged because production data already contains `avatar_url`.

**Tech Stack:** React 18, TypeScript 5.6, React DOM Portal, Vitest, Testing Library, Vite, CSS.

## Global Constraints

- Preserve all existing capability labels, callbacks, keyboard navigation, and focus return behavior.
- Prefer upward menu placement; constrain the menu to the brain-stage boundary and use internal scrolling when necessary.
- Do not add an avatar synchronization request or change the backend account API.
- Keep nickname-initial fallback behavior when an avatar is absent or fails to load.
- Deploy only the exact verified commit through a new immutable release directory and atomic `/home/admin/dyflow` symlink switch.

---

### Task 1: Portal-based capability menu positioning

**Files:**
- Modify: `frontend/src/components/brain/CapabilityLauncher.tsx`
- Modify: `frontend/src/components/brain/CapabilityLauncher.test.tsx`
- Modify: `frontend/src/styles/brain-v2.css`

**Interfaces:**
- Consumes: `triggerRef`, `.tz-brain-stage` bounds, `window.innerWidth`, and `window.innerHeight`.
- Produces: `CapabilityMenuPosition` applied as inline fixed-position styles to the portal menu.

- [ ] **Step 1: Write the failing portal and boundary-position test**

Add `waitFor` to the Testing Library import, replace `afterEach(cleanup)` with:

```tsx
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
```

Then add:

```tsx
it("portals the menu outside the clipped brain stage and constrains it above the trigger", async () => {
  const stage = document.createElement("main");
  stage.className = "tz-brain-stage";
  document.body.appendChild(stage);

  render(
    <CapabilityLauncher skills={[accountInspection]} onSelectSkill={vi.fn()} />,
    { container: stage },
  );

  vi.stubGlobal("innerHeight", 900);
  vi.stubGlobal("innerWidth", 1200);
  const trigger = screen.getByRole("button", { name: "添加能力或材料" });
  vi.spyOn(trigger, "getBoundingClientRect").mockReturnValue({
    x: 430,
    y: 550,
    top: 550,
    right: 464,
    bottom: 584,
    left: 430,
    width: 34,
    height: 34,
    toJSON: () => ({}),
  });
  vi.spyOn(stage, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 122,
    top: 122,
    right: 1200,
    bottom: 900,
    left: 0,
    width: 1200,
    height: 778,
    toJSON: () => ({}),
  });

  fireEvent.click(trigger);

  const menu = await screen.findByRole("menu", { name: "能力与材料" });
  expect(menu.parentElement).toBe(document.body);
  await waitFor(() => {
    expect(menu).toHaveStyle({
      position: "fixed",
      left: "430px",
      bottom: "359px",
      maxHeight: "419px",
    });
  });
});
```

The expected values use a 9px trigger gap: `900 - 550 + 9 = 359` bottom and `550 - 122 - 9 = 419` available height.

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
pnpm test -- src/components/brain/CapabilityLauncher.test.tsx
```

Expected: FAIL because the menu remains inside `.dy-brain-capability-launcher` and has no fixed-position inline style.

- [ ] **Step 3: Implement portal mounting and measured placement**

In `CapabilityLauncher.tsx`, import `createPortal`, `CSSProperties`, and `useCallback`. Add:

```tsx
type CapabilityMenuPosition = Pick<CSSProperties, "top" | "bottom" | "left" | "maxHeight">;

const MENU_WIDTH = 236;
const MENU_GAP = 9;
const VIEWPORT_GUTTER = 12;

function measureCapabilityMenuPosition(trigger: HTMLButtonElement): CapabilityMenuPosition {
  const triggerRect = trigger.getBoundingClientRect();
  const boundaryRect = trigger.closest(".tz-brain-stage")?.getBoundingClientRect();
  const boundaryTop = Math.max(boundaryRect?.top ?? VIEWPORT_GUTTER, VIEWPORT_GUTTER);
  const boundaryBottom = Math.min(
    boundaryRect?.bottom ?? window.innerHeight - VIEWPORT_GUTTER,
    window.innerHeight - VIEWPORT_GUTTER,
  );
  const availableAbove = Math.max(0, triggerRect.top - boundaryTop - MENU_GAP);
  const availableBelow = Math.max(0, boundaryBottom - triggerRect.bottom - MENU_GAP);
  const left = Math.max(
    VIEWPORT_GUTTER,
    Math.min(triggerRect.left, window.innerWidth - MENU_WIDTH - VIEWPORT_GUTTER),
  );

  if (availableAbove >= availableBelow) {
    return {
      left,
      bottom: window.innerHeight - triggerRect.top + MENU_GAP,
      maxHeight: availableAbove,
    };
  }
  return {
    left,
    top: triggerRect.bottom + MENU_GAP,
    maxHeight: availableBelow,
  };
}
```

Store `menuPosition` in component state. When the menu opens, calculate it immediately and recalculate on `resize` and capture-phase `scroll`. Run the existing first-item focus effect after both `open` and `menuPosition` are set so portal mounting does not change keyboard focus behavior. Render the existing menu as:

```tsx
{open && menuPosition
  ? createPortal(
      <div
        ref={menuRef}
        className="dy-brain-capability-menu"
        role="menu"
        aria-label="能力与材料"
        style={{ position: "fixed", ...menuPosition }}
      >
        {/* existing groups and items unchanged */}
      </div>,
      document.body,
    )
  : null}
```

Keep the existing outside-click, focus, Escape, arrow-key, Enter, and Space behavior unchanged.

- [ ] **Step 4: Unscope portal menu CSS from `.tz-brain-thread`**

In `brain-v2.css`, keep launcher and trigger selectors scoped to `.tz-brain-thread`, but change menu subtree selectors to:

```css
.dy-brain-capability-menu {
  z-index: 1000;
  display: grid;
  width: 236px;
  overflow-y: auto;
  padding: 8px;
  border: 1px solid var(--tz-line);
  border-radius: 12px;
  background: var(--tz-surface);
  box-shadow: 0 14px 32px rgba(42, 35, 27, 0.16);
}

.dy-brain-capability-menu .dy-brain-capability-group { /* existing declarations */ }
.dy-brain-capability-menu .dy-brain-capability-group + .dy-brain-capability-group { /* existing declarations */ }
.dy-brain-capability-menu .dy-brain-capability-group h3 { /* existing declarations */ }
.dy-brain-capability-menu .dy-brain-capability-items { /* existing declarations */ }
.dy-brain-capability-menu .dy-brain-capability-item { /* existing declarations */ }
```

Apply the same `.dy-brain-capability-menu` prefix to hover, focus, disabled, title, and description selectors. Remove the old absolute positioning, `bottom`, `left`, and viewport-based `max-height` declarations because positioning is now inline and boundary-aware.

- [ ] **Step 5: Run capability launcher and composer tests**

Run:

```powershell
pnpm test -- src/components/brain/CapabilityLauncher.test.tsx src/components/brain/BrainComposer.test.tsx
```

Expected: all tests pass, including keyboard navigation and outside-click closure.

- [ ] **Step 6: Commit the independently testable menu fix**

```powershell
git add -- frontend/src/components/brain/CapabilityLauncher.tsx frontend/src/components/brain/CapabilityLauncher.test.tsx frontend/src/styles/brain-v2.css
git commit -m "fix: keep capability menu inside visible stage"
```

### Task 2: Selected-account avatar

**Files:**
- Modify: `frontend/src/components/shell/AccountContext.tsx`
- Modify: `frontend/src/components/shell/AccountContext.test.tsx`
- Modify: `frontend/src/styles/app-shell.css`

**Interfaces:**
- Consumes: `current: Account | null` and existing `AccountAvatar({ account })`.
- Produces: a real avatar in `.tz-account-trigger` when `current` is selected, with the existing initial fallback on image failure.

- [ ] **Step 1: Write the failing selected-avatar and fallback tests**

Add `within` to the Testing Library import and add:

```tsx
it("shows the synchronized avatar in the selected-account trigger", () => {
  render(
    <AccountContext
      accounts={accounts}
      platform="douyin"
      accountId={1}
      onChange={vi.fn()}
    />,
  );

  const trigger = screen.getByRole("button", { name: "当前账号" });
  expect(within(trigger).getByRole("img", { name: "账号一" })).toHaveAttribute(
    "src",
    "https://example.com/account-one.png",
  );
});

it("falls back to the account initial when the selected avatar fails", () => {
  render(
    <AccountContext
      accounts={accounts}
      platform="douyin"
      accountId={1}
      onChange={vi.fn()}
    />,
  );

  const trigger = screen.getByRole("button", { name: "当前账号" });
  fireEvent.error(within(trigger).getByRole("img", { name: "账号一" }));
  expect(within(trigger).queryByRole("img", { name: "账号一" })).not.toBeInTheDocument();
  expect(within(trigger).getByText("账")).toBeVisible();
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
pnpm test -- src/components/shell/AccountContext.test.tsx
```

Expected: FAIL because `.tz-account-trigger` still contains `.tz-platform-mark` even when an account is selected.

- [ ] **Step 3: Reuse `AccountAvatar` in the trigger**

Replace the fixed mark in `AccountContext.tsx` with:

```tsx
{current ? (
  <AccountAvatar account={current} compact />
) : (
  <span className="tz-platform-mark">抖</span>
)}
```

Extend the private component interface:

```tsx
function AccountAvatar({ account, compact = false }: { account: Account; compact?: boolean }) {
  // existing failure state
  return (
    <span className={`tz-account-avatar${compact ? " is-compact" : ""}`}>
      {/* existing image/fallback behavior */}
    </span>
  );
}
```

- [ ] **Step 4: Add compact trigger-avatar styling**

In `app-shell.css`, add:

```css
.tz-account-avatar.is-compact {
  width: 23px;
  height: 23px;
  border-radius: 7px;
}
```

Do not change the existing 32px avatar used by dropdown items.

- [ ] **Step 5: Run account-context tests**

Run:

```powershell
pnpm test -- src/components/shell/AccountContext.test.tsx
```

Expected: all selected, unselected, stale-id, dropdown, real-avatar, and image-fallback tests pass.

- [ ] **Step 6: Commit the independently testable avatar fix**

```powershell
git add -- frontend/src/components/shell/AccountContext.tsx frontend/src/components/shell/AccountContext.test.tsx frontend/src/styles/app-shell.css
git commit -m "fix: show synchronized avatar for current account"
```

### Task 3: Full verification and production release

**Files:**
- Verify: `frontend/`
- Deploy: repository archive built from the final verified commit

**Interfaces:**
- Consumes: Task 1 and Task 2 commits.
- Produces: a healthy production deployment at `https://tzxai.top`.

- [ ] **Step 1: Run the full frontend quality gate**

```powershell
pnpm test
pnpm lint
pnpm build
```

Expected: Vitest, ESLint, TypeScript, and Vite all exit with status 0.

- [ ] **Step 2: Verify the final diff and commit state**

```powershell
git diff --check
git status --short
git log -4 --oneline
```

Expected: no whitespace errors, no uncommitted files, and both fix commits follow the design commit.

- [ ] **Step 3: Browser-check a production-equivalent build**

Verify:

- The menu is portaled under `document.body`.
- Its top is at or below the brain-stage top, and its bottom stays above the trigger.
- At a short viewport, the menu scrolls internally and its last item remains reachable.
- The current-account trigger contains the real Douyin avatar.
- Image failure replaces the image with the nickname initial.
- No new browser console errors or warnings appear.

- [ ] **Step 4: Package the exact verified commit**

```powershell
$commit = (git rev-parse --short HEAD).Trim()
$archive = "C:\tmp\dyflow-20260730-menu-avatar-$commit.tar.gz"
git archive --format=tar.gz -o $archive HEAD
Get-FileHash -Algorithm SHA256 $archive
```

Confirm the archive contains the four changed frontend source/test areas, `docker-compose.prod.yml`, and no real `.env`.

- [ ] **Step 5: Deploy through a new immutable release directory**

Upload the archive and checksum to `/home/admin/releases/`, verify SHA-256 on the server, extract to `/home/admin/releases/dyflow-20260730-menu-avatar-<commit>`, copy the active release `.env`, build the Compose images, and run:

```bash
docker compose -f docker-compose.prod.yml run -T --rm backend alembic upgrade head
```

Create a unique temporary symlink to the new release, atomically replace `/home/admin/dyflow` with `mv -Tf`, then run:

```bash
docker compose -f docker-compose.prod.yml up -d --remove-orphans
```

Keep the previously active release path unchanged as the rollback target.

- [ ] **Step 6: Verify production health and UI**

```powershell
curl.exe -fsS https://tzxai.top/api/health/ready
curl.exe -fsS -o NUL -w "root_status=%{http_code} tls_verify=%{ssl_verify_result}`n" https://tzxai.top/
```

Expected:

- readiness reports database and Redis healthy
- root status is `200`
- TLS verification result is `0`
- all Compose services are healthy or running
- Alembic reports the current head revision
- the production browser shows the complete capability menu and real selected-account avatar
- no new production console errors or warnings appear

- [ ] **Step 7: Record final deployment evidence**

Capture the deployed commit, release path, previous rollback path, Compose status, migration head, public health result, and browser acceptance findings in the final handoff.
