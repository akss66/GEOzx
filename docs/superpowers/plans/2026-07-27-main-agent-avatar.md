# Main Agent Avatar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every main Agent identity mark with the supplied black swirl avatar while preserving the Tongzhouxing platform logo.

**Architecture:** Store the cleaned artwork as a dedicated transparent public asset and expose its path from the existing `AgentAvatar` component. Reuse that component for main Agent identity surfaces so branding and Agent identity cannot drift independently.

**Tech Stack:** React 18, TypeScript, Vitest, Testing Library, CSS, transparent PNG.

## Global Constraints

- `frontend/public/logo.png` and every platform-brand use of `/logo.png` remain unchanged.
- The main Agent avatar is black and uses a real Alpha channel; the source JPG checkerboard must not remain.
- Expert Agent icons and backend APIs are out of scope.
- Existing user changes in `BrainHome.tsx`, `BrainHome.test.tsx`, and `brain-v2.css` must be preserved.

---

### Task 1: Produce the transparent main Agent asset

**Files:**
- Source: `C:/Users/AKSSINA/AppData/Local/Temp/codex-clipboard-03a6b7d7-cc5b-4b4a-89d1-b59f45abe138.jpg`
- Create: `frontend/public/main-agent-avatar.png`

**Interfaces:**
- Consumes: the user-supplied 1024×1024 JPG containing a black swirl over a baked checkerboard.
- Produces: `/main-agent-avatar.png`, a square PNG with a black mark, transparent background, and safe edge padding.

- [ ] **Step 1: Generate the cleaned asset**

Use the image editing workflow with this exact instruction:

```text
Remove the gray-and-white checkerboard completely and make it true transparency.
Preserve the supplied black six-part swirl geometry exactly; do not redesign,
add text, add shadows, recolor, or add a background. Center the mark on a square
canvas with approximately 8% transparent safe padding on every side. Output PNG.
```

- [ ] **Step 2: Validate the file format**

Run a `System.Drawing.Bitmap` inspection and assert:

```text
Width == Height
PixelFormat contains "Argb"
corner pixel alpha == 0
center artwork pixel alpha > 0
```

Expected: all four conditions are true.

- [ ] **Step 3: Inspect the asset visually**

Render the PNG on both white and dark neutral backgrounds. Confirm there is no checkerboard residue, gray halo, crop, shadow, or geometry change.

- [ ] **Step 4: Commit the asset**

```powershell
git add -- frontend/public/main-agent-avatar.png
git commit -m "feat: add main agent avatar asset"
```

### Task 2: Separate main Agent identity from platform branding

**Files:**
- Modify: `frontend/src/components/agents/AgentAvatar.tsx`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Test: `frontend/src/pages/BrainHome.test.tsx`

**Interfaces:**
- Produces: `MAIN_AGENT_AVATAR_SRC` with the exact value `"/main-agent-avatar.png"`.
- Consumes: `AgentAvatar({ code: "00-decision" })` and the welcome-state identity in `BrainHome`.

- [ ] **Step 1: Write the failing identity test**

Change the existing test name and expectation to:

```tsx
it("uses the dedicated avatar for the main Agent identity", async () => {
  // Preserve the existing task setup and render steps.
  const identities = screen.getAllByRole("img", { name: "主 Agent" });
  expect(identities.length).toBeGreaterThan(0);
  expect(identities[0].querySelector("img")).toHaveAttribute(
    "src",
    "/main-agent-avatar.png",
  );
});
```

Add a welcome-state assertion against the image inside `.tz-brain-welcome__agent`:

```tsx
expect(
  document.querySelector(".tz-brain-welcome__agent img"),
).toHaveAttribute("src", "/main-agent-avatar.png");
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
pnpm vitest run src/pages/BrainHome.test.tsx
```

Expected: FAIL because the old implementation still renders `/logo.png`.

- [ ] **Step 3: Implement the shared identity path**

In `AgentAvatar.tsx`, export and consume:

```tsx
export const MAIN_AGENT_AVATAR_SRC = "/main-agent-avatar.png";

// main Agent branch
<img src={MAIN_AGENT_AVATAR_SRC} alt="" />
```

In `BrainHome.tsx`, import the constant with `AgentAvatar` and use:

```tsx
<img src={MAIN_AGENT_AVATAR_SRC} alt="" />
```

Do not change `AppShell.tsx`, `Login.tsx`, or `frontend/public/logo.png`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```powershell
pnpm vitest run src/pages/BrainHome.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit the identity separation**

```powershell
git add -- frontend/src/components/agents/AgentAvatar.tsx frontend/src/pages/BrainHome.tsx frontend/src/pages/BrainHome.test.tsx
git commit -m "feat: separate main agent avatar from platform logo"
```

### Task 3: Remove the legacy main Agent message icon

**Files:**
- Modify: `frontend/src/components/brain/AgentOrchestration.tsx`
- Test: `frontend/src/components/brain/AgentOrchestration.test.tsx`
- Modify only if visual inspection requires it: `frontend/src/index.css`

**Interfaces:**
- Consumes: `AgentAvatar` with `code="00-decision"`.
- Produces: the legacy orchestration master card using the same accessible main Agent identity as the active conversation.

- [ ] **Step 1: Write the failing orchestration test**

Inside the populated orchestration test, add:

```tsx
const mainAgentAvatar = screen.getByRole("img", { name: "主 Agent" });
expect(mainAgentAvatar.querySelector("img")).toHaveAttribute(
  "src",
  "/main-agent-avatar.png",
);
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
pnpm vitest run src/components/brain/AgentOrchestration.test.tsx
```

Expected: FAIL because the legacy card renders `MessageOutlined` without a main Agent image.

- [ ] **Step 3: Reuse `AgentAvatar`**

Remove the `MessageOutlined` import, import `AgentAvatar`, and replace the legacy wrapper with:

```tsx
<AgentAvatar code="00-decision" className="dy-agent-master-avatar" />
```

If the image does not fit during browser inspection, add only this scoped CSS:

```css
.dy-agent-master-avatar img {
  width: 72%;
  height: 72%;
  object-fit: contain;
}
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
pnpm vitest run src/components/brain/AgentOrchestration.test.tsx src/pages/BrainHome.test.tsx
```

Expected: both test files PASS.

- [ ] **Step 5: Commit the legacy identity cleanup**

```powershell
git add -- frontend/src/components/brain/AgentOrchestration.tsx frontend/src/components/brain/AgentOrchestration.test.tsx frontend/src/index.css
git commit -m "feat: unify main agent identity surfaces"
```

### Task 4: Verify behavior and presentation

**Files:**
- Verify: `frontend/public/logo.png`
- Verify: `frontend/public/main-agent-avatar.png`
- Verify: `frontend/src/components/AppShell.tsx`
- Verify: `frontend/src/pages/Login.tsx`

**Interfaces:**
- Consumes: the completed asset and component changes.
- Produces: evidence that the new main Agent identity is correct without a platform-brand regression.

- [ ] **Step 1: Run the full frontend test suite**

```powershell
pnpm test
```

Expected: zero failed tests.

- [ ] **Step 2: Run the production build**

```powershell
pnpm build
```

Expected: TypeScript checking and Vite build exit with code 0.

- [ ] **Step 3: Verify source references**

```powershell
rg -n 'main-agent-avatar|/logo.png' src public
```

Expected:

- Main Agent identity code references `/main-agent-avatar.png`.
- `AppShell.tsx` and `Login.tsx` still reference `/logo.png`.
- `logo.png` remains present and unchanged.

- [ ] **Step 4: Verify in a real browser**

Inspect the welcome state and an active conversation at desktop and narrow viewport widths, in light and dark themes. Confirm the black avatar is crisp, centered, distinguishable from the red platform brand, and has no checkerboard or halo.

- [ ] **Step 5: Commit any verification-only CSS adjustment**

If and only if browser inspection required the scoped CSS from Task 3:

```powershell
git add -- frontend/src/index.css
git commit -m "fix: align main agent avatar artwork"
```

