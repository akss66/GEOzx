# Main Agent Avatar Frameless Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the visible frame and backing plate from every main Agent avatar while preserving identity, sizing, alignment, and platform branding.

**Architecture:** Keep `AgentAvatar` and the PNG resource unchanged. Make the main Agent variant frameless in the shared avatar CSS so every consumer inherits the same treatment, then verify the result in the welcome state, conversation state, page header, and orchestration view.

**Tech Stack:** React 18, TypeScript, CSS, Vitest, Vite, browser runtime validation.

## Global Constraints

- The main Agent avatar must have no visible border or background plate.
- The black mark must remain complete and undistorted.
- Existing contextual slot sizes and text alignment must remain unchanged.
- Expert avatars and the platform `/logo.png` presentation must remain unchanged.
- Do not modify the avatar PNG, component structure, or business logic.

---

### Task 1: Make the shared main Agent avatar frameless

**Files:**
- Modify: `frontend/src/styles/app-shell.css:112-120`
- Verify: `frontend/src/components/agents/AgentAvatar.tsx`
- Verify: `frontend/src/styles/brain-v2.css`
- Verify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `.tz-agent-avatar.is-main-agent` emitted by `AgentAvatar`.
- Produces: a transparent, borderless avatar container whose image fills the existing contextual slot.

- [ ] **Step 1: Capture the failing visual baseline**

Open `http://localhost:5173/`, inspect a main Agent avatar, and confirm the current computed style reports a non-zero border and a non-transparent background.

Expected before implementation:

```text
borderTopWidth: "1px"
backgroundColor: non-transparent
image width: 72% of the avatar slot
```

- [ ] **Step 2: Implement the minimal shared CSS change**

Replace the main Agent rules with:

```css
.tz-agent-avatar.is-main-agent,
.dy-agent-master-avatar.tz-agent-avatar.is-main-agent {
  border: 0;
  background: transparent;
}

.tz-agent-avatar.is-main-agent img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
```

The combined orchestration selector has enough specificity to override the legacy light and dark backing-plate rules without `!important`.

- [ ] **Step 3: Run focused automated tests**

Run:

```powershell
pnpm.cmd exec vitest run src/pages/BrainHome.test.tsx src/components/brain/AgentOrchestration.test.tsx
```

Expected: both test files pass with no failed tests.

- [ ] **Step 4: Run the production build**

Run:

```powershell
pnpm.cmd build
```

Expected: TypeScript checking and Vite production build exit with code 0.

- [ ] **Step 5: Verify the rendered interface**

In the browser, verify:

```text
main Agent borderTopWidth = "0px"
main Agent backgroundColor = "rgba(0, 0, 0, 0)"
main Agent image src = "/main-agent-avatar.png"
platform logo src = "/logo.png"
all inspected images are loaded
```

Capture the welcome state and conversation state at desktop width. Confirm that the black mark sits directly on the page, remains centered, and does not change adjacent text alignment. Check browser logs for new errors.

- [ ] **Step 6: Commit the isolated CSS change**

```powershell
git add -- frontend/src/styles/app-shell.css
git commit -m "style: remove main agent avatar frame"
```
