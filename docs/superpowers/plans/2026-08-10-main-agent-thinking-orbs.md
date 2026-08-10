# Main Agent Thinking Orbs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add phase-specific Thinking Orbs to the currently active main-Agent WorkTurn while preserving all global, header, account, and terminal-state avatars.

**Architecture:** Keep backend events and `TurnPhase` as the single state source. Project the phase into `WorkTurnViewModel`, map it through one pure function, render the orb through a WorkTurn-only status-avatar boundary, and let `TurnStream` select at most one active turn for animation. Existing status copy, progress, disclosures, artifacts, actions, and the shared `AgentAvatar` remain unchanged.

**Tech Stack:** React 18.3, TypeScript 5.6, Vite 6, Vitest 3, Testing Library, `thinking-orbs@0.2.0`.

## Global Constraints

- Only the avatar inside the currently active main-Agent WorkTurn may become a Thinking Orb.
- The page-header operations-brain avatar, upper-right user/account avatar, global launcher, expert avatars, and all terminal/history WorkTurns must retain their current rendering.
- Orb state must derive from `ConversationTurn.turn_phase`; never infer it from Chinese copy or expose chain-of-thought.
- Preserve the existing Chinese activity label as the sole `aria-live="polite"` status; the orb is a labelled image, not a second live region.
- Use `ThinkingOrb` with `size={64}` and `theme="light"`; constrain its visual box to the existing 32px WorkTurn avatar footprint.
- Render no more than one animated orb per thread, choosing the last active WorkTurn when malformed data contains multiple active turns.
- Unknown or missing active phases map to `working`; waiting, completed, blocked, failed, and cancelled states render the existing static main-Agent avatar.
- Respect the library's reduced-motion, offscreen-pause, hidden-tab-pause, and DPR behavior; do not add a second CSS pulse, glow, gradient, or red recoloring.
- If orb rendering throws, restore the existing static avatar without hiding WorkTurn text, progress, deliverables, or actions.
- Do not modify the V4.1 evaluation plan, backend contracts, SSE event schema, model prompts, routing, database schema, or deployment topology.
- Keep `docs/ideas/` and `docs/intent/` untouched and unstaged.

---

## File Structure

- `frontend/src/components/brain/workTurnOrbState.ts`: pure `TurnPhase` to orb-state mapping.
- `frontend/src/components/brain/workTurnOrbState.test.ts`: exhaustive mapping and fallback tests.
- `frontend/src/components/brain/MainAgentStatusAvatar.tsx`: WorkTurn-only orb/static-avatar switch and render error boundary.
- `frontend/src/components/brain/MainAgentStatusAvatar.test.tsx`: active, inactive, label, phase, and failure-fallback behavior.
- `frontend/src/components/brain/workTurnProjection.ts`: retain the source phase in `WorkTurnViewModel`.
- `frontend/src/components/brain/workTurnProjection.test.ts`: verify phase projection.
- `frontend/src/components/brain/WorkTurnCard.tsx`: consume the WorkTurn-only status avatar.
- `frontend/src/components/brain/WorkTurnCard.test.tsx`: verify active-to-terminal continuity and unchanged static states.
- `frontend/src/components/brain/TurnStream.tsx`: select the last active WorkTurn as the sole orb owner.
- `frontend/src/components/brain/TurnStream.test.tsx`: verify one-orb maximum across malformed multiple-running-turn input.
- `frontend/src/types.ts`: add optional `phase` to `WorkTurnViewModel`.
- `frontend/src/styles/brain-v2.css`: size the canvas and remove the obsolete avatar pulse.
- `frontend/package.json`, `frontend/package-lock.json`: pin `thinking-orbs@0.2.0`.

---

### Task 1: Phase Projection and Deterministic Orb Mapping

**Files:**
- Create: `frontend/src/components/brain/workTurnOrbState.ts`
- Create: `frontend/src/components/brain/workTurnOrbState.test.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/brain/workTurnProjection.ts`
- Modify: `frontend/src/components/brain/workTurnProjection.test.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:**
- Consumes: existing `TurnPhase` and `ConversationTurn.turn_phase`.
- Produces: `ThinkingOrbVisualState`, `workTurnOrbState(phase?: TurnPhase): ThinkingOrbVisualState`, and `WorkTurnViewModel.phase?: TurnPhase`.

- [ ] **Step 1: Write the failing mapping tests**

Create `frontend/src/components/brain/workTurnOrbState.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { workTurnOrbState } from "./workTurnOrbState";

describe("workTurnOrbState", () => {
  it.each([
    ["understanding", "listening"],
    ["reading_data", "searching"],
    ["consulting_experts", "weaving"],
    ["quality_review", "solving"],
    ["composing_artifact", "composing"],
  ] as const)("maps %s to %s", (phase, state) => {
    expect(workTurnOrbState(phase)).toBe(state);
  });

  it("uses working for phases without an animated business meaning", () => {
    expect(workTurnOrbState()).toBe("working");
    expect(workTurnOrbState("waiting_approval")).toBe("working");
    expect(workTurnOrbState("completed")).toBe("working");
    expect(workTurnOrbState("failed")).toBe("working");
  });
});
```

- [ ] **Step 2: Run the mapping test and verify the red state**

Run:

```powershell
cd frontend
npm.cmd test -- src/components/brain/workTurnOrbState.test.ts
```

Expected: FAIL because `workTurnOrbState.ts` does not exist.

- [ ] **Step 3: Install the exact MIT dependency**

Run:

```powershell
cd frontend
npm.cmd install thinking-orbs@0.2.0 --save-exact
```

Expected: `package.json` contains `"thinking-orbs": "0.2.0"`; the lockfile records the same version and React 18 peer requirements remain satisfied.

- [ ] **Step 4: Implement the pure mapping**

Create `frontend/src/components/brain/workTurnOrbState.ts`:

```ts
import type { TurnPhase } from "../../types";

export type ThinkingOrbVisualState =
  | "working"
  | "searching"
  | "solving"
  | "listening"
  | "connecting"
  | "weaving"
  | "composing"
  | "breathing"
  | "shaping";

const ORB_STATE_BY_PHASE: Partial<Record<TurnPhase, ThinkingOrbVisualState>> = {
  understanding: "listening",
  reading_data: "searching",
  consulting_experts: "weaving",
  quality_review: "solving",
  composing_artifact: "composing",
};

export function workTurnOrbState(phase?: TurnPhase): ThinkingOrbVisualState {
  return (phase && ORB_STATE_BY_PHASE[phase]) || "working";
}
```

- [ ] **Step 5: Project the source phase into the view model and test it**

Add `phase?: TurnPhase` immediately after `status` in `WorkTurnViewModel`, add `phase: turn.turn_phase` in `projectWorkTurn`, and extend the existing running-turn projection assertion:

```ts
expect(projectWorkTurn(turn({
  status: "running",
  turn_phase: "reading_data",
}))).toMatchObject({
  status: "working",
  phase: "reading_data",
});
```

- [ ] **Step 6: Run focused mapping and projection tests**

Run:

```powershell
cd frontend
npm.cmd test -- src/components/brain/workTurnOrbState.test.ts src/components/brain/workTurnProjection.test.ts
```

Expected: PASS with no snapshots or unrelated source files changed.

- [ ] **Step 7: Commit Task 1**

```powershell
git add -- frontend/package.json frontend/package-lock.json frontend/src/types.ts frontend/src/components/brain/workTurnOrbState.ts frontend/src/components/brain/workTurnOrbState.test.ts frontend/src/components/brain/workTurnProjection.ts frontend/src/components/brain/workTurnProjection.test.ts
git commit -m "feat: map main agent phases to thinking orbs"
```

---

### Task 2: WorkTurn-Only Status Avatar with Static Fallback

**Files:**
- Create: `frontend/src/components/brain/MainAgentStatusAvatar.tsx`
- Create: `frontend/src/components/brain/MainAgentStatusAvatar.test.tsx`

**Interfaces:**
- Consumes: `workTurnOrbState(phase?: TurnPhase)`, existing `AgentAvatar`, and `ThinkingOrb` from `thinking-orbs`.
- Produces: `MainAgentStatusAvatar({ showThinkingOrb, phase, identity, activityLabel, className }): JSX.Element`.

- [ ] **Step 1: Write failing active, inactive, accessibility, and fallback tests**

Create `frontend/src/components/brain/MainAgentStatusAvatar.test.tsx` with a controllable module mock:

```tsx
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const orbMock = vi.hoisted(() => ({ shouldThrow: false }));

vi.mock("thinking-orbs", () => ({
  ThinkingOrb: (props: Record<string, unknown>) => {
    if (orbMock.shouldThrow) throw new Error("canvas unavailable");
    return <canvas data-testid="thinking-orb" data-state={props.state} data-theme={props.theme} aria-label={String(props["aria-label"])} />;
  },
}));

import { MainAgentStatusAvatar } from "./MainAgentStatusAvatar";

describe("MainAgentStatusAvatar", () => {
  afterEach(() => {
    orbMock.shouldThrow = false;
    cleanup();
  });

  it("renders the phase orb only when selected as active", () => {
    const view = render(<MainAgentStatusAvatar showThinkingOrb phase="reading_data" identity="运营大脑" activityLabel="正在核对已导入的数据范围" className="tz-work-turn__avatar" />);
    expect(screen.getByTestId("thinking-orb")).toHaveAttribute("data-state", "searching");
    expect(screen.getByTestId("thinking-orb")).toHaveAttribute("data-theme", "light");
    expect(screen.getByLabelText("正在核对已导入的数据范围")).toBeVisible();

    view.rerender(<MainAgentStatusAvatar showThinkingOrb={false} phase="completed" identity="运营大脑" activityLabel={null} className="tz-work-turn__avatar" />);
    expect(screen.queryByTestId("thinking-orb")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: "运营大脑" })).toBeVisible();
  });

  it("falls back to the static avatar when orb rendering fails", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    orbMock.shouldThrow = true;
    render(<MainAgentStatusAvatar showThinkingOrb phase="quality_review" identity="运营大脑" activityLabel="正在核验结论与数据依据" />);
    expect(screen.getByRole("img", { name: "运营大脑" })).toBeVisible();
  });
});
```

- [ ] **Step 2: Run the component test and verify the red state**

Run:

```powershell
cd frontend
npm.cmd test -- src/components/brain/MainAgentStatusAvatar.test.tsx
```

Expected: FAIL because `MainAgentStatusAvatar.tsx` does not exist.

- [ ] **Step 3: Implement the WorkTurn-only component and error boundary**

Create `frontend/src/components/brain/MainAgentStatusAvatar.tsx`:

```tsx
import { Component, type ReactNode } from "react";
import { ThinkingOrb } from "thinking-orbs";

import type { TurnPhase } from "../../types";
import { AgentAvatar } from "../agents/AgentAvatar";
import { workTurnOrbState } from "./workTurnOrbState";

type Props = {
  showThinkingOrb: boolean;
  phase?: TurnPhase;
  identity: string;
  activityLabel?: string | null;
  className?: string;
};

type BoundaryProps = { fallback: ReactNode; children: ReactNode };

class OrbRenderBoundary extends Component<BoundaryProps, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export function MainAgentStatusAvatar({
  showThinkingOrb,
  phase,
  identity,
  activityLabel,
  className = "",
}: Props) {
  const fallback = <AgentAvatar code="00-decision" className={className} label={identity} />;
  if (!showThinkingOrb) return fallback;

  return (
    <OrbRenderBoundary fallback={fallback}>
      <span className={["tz-main-agent-status-avatar", className].filter(Boolean).join(" ")} data-thinking-orb="true">
        <ThinkingOrb
          state={workTurnOrbState(phase)}
          size={64}
          theme="light"
          aria-label={activityLabel || `${identity}正在工作`}
        />
      </span>
    </OrbRenderBoundary>
  );
}
```

React continues to report the render error through its standard error channel; the boundary only changes the user-visible result to the existing avatar.

- [ ] **Step 4: Run the component test**

Run:

```powershell
cd frontend
npm.cmd test -- src/components/brain/MainAgentStatusAvatar.test.tsx
```

Expected: PASS; active state exposes one canvas, terminal state and render failure expose the existing avatar image.

- [ ] **Step 5: Run TypeScript and lint on the new boundary**

Run:

```powershell
cd frontend
npx.cmd tsc --noEmit
npx.cmd eslint src/components/brain/MainAgentStatusAvatar.tsx src/components/brain/MainAgentStatusAvatar.test.tsx
```

Expected: both commands exit 0. If the library's exported `state` type is narrower than the local union, narrow `ThinkingOrbVisualState` to the exact nine values already listed; do not cast to `any`.

- [ ] **Step 6: Commit Task 2**

```powershell
git add -- frontend/src/components/brain/MainAgentStatusAvatar.tsx frontend/src/components/brain/MainAgentStatusAvatar.test.tsx
git commit -m "feat: render resilient main agent thinking orb"
```

---

### Task 3: Single-Active WorkTurn Integration and Visual Verification

**Files:**
- Modify: `frontend/src/components/brain/TurnStream.tsx`
- Modify: `frontend/src/components/brain/TurnStream.test.tsx`
- Modify: `frontend/src/components/brain/WorkTurnCard.tsx`
- Modify: `frontend/src/components/brain/WorkTurnCard.test.tsx`
- Modify: `frontend/src/styles/brain-v2.css`

**Interfaces:**
- Consumes: `WorkTurnViewModel.phase`, `MainAgentStatusAvatar`, and `view.presentation.isActive`.
- Produces: `WorkTurnCard` prop `showThinkingOrb?: boolean`; `TurnStream` guarantees that at most the last active projected turn receives `true`.

- [ ] **Step 1: Write failing WorkTurn transition and single-owner tests**

In `WorkTurnCard.test.tsx`, mock `thinking-orbs` as a labelled canvas and extend the existing continuity test:

```tsx
vi.mock("thinking-orbs", () => ({
  ThinkingOrb: ({ state, ...props }: { state: string; [key: string]: unknown }) => (
    <canvas data-testid="thinking-orb" data-state={state} aria-label={String(props["aria-label"])} />
  ),
}));

const rendered = render(<WorkTurnCard view={{ ...workingTurn, phase: "reading_data" }} showThinkingOrb />);
expect(screen.getAllByTestId("thinking-orb")).toHaveLength(1);
rendered.rerender(<WorkTurnCard view={{ ...completedTurn, phase: "completed" }} showThinkingOrb={false} />);
expect(screen.queryByTestId("thinking-orb")).not.toBeInTheDocument();
expect(screen.getByRole("img", { name: "运营大脑" })).toBeVisible();
```

In `TurnStream.test.tsx`, add a thread containing two `running` turns and assert:

```tsx
expect(screen.getAllByTestId("thinking-orb")).toHaveLength(1);
expect(screen.getAllByTestId("work-turn").at(-1))
  .toHaveAttribute("data-has-thinking-orb", "true");
```

Also retain the existing assertion that only one visible “运营大脑” identity label exists per WorkTurn and no duplicate “思考中” copy is introduced.

- [ ] **Step 2: Run the WorkTurn tests and verify the red state**

Run:

```powershell
cd frontend
npm.cmd test -- src/components/brain/WorkTurnCard.test.tsx src/components/brain/TurnStream.test.tsx
```

Expected: FAIL because `showThinkingOrb` and the status avatar integration do not exist.

- [ ] **Step 3: Integrate the status avatar into `WorkTurnCard`**

Replace the direct `AgentAvatar` import with `MainAgentStatusAvatar` and extend the exact function signature:

```tsx
export function WorkTurnCard({
  view,
  evidenceSummary = [],
  technicalLog = [],
  deliverables,
  businessActions,
  sourceStatus,
  showThinkingOrb = false,
}: {
  view: WorkTurnViewModel;
  evidenceSummary?: string[];
  technicalLog?: string[];
  deliverables?: ReactNode;
  businessActions?: ReactNode;
  sourceStatus?: string;
  showThinkingOrb?: boolean;
}) {
```

Add the owner attribute next to the current `data-turn-status` attribute:

```tsx
<article
  className="tz-work-turn"
  data-testid="work-turn"
  data-turn-id={view.turnId ?? undefined}
  data-turn-key={view.key}
  data-turn-status={sourceStatus ?? view.status}
  data-has-thinking-orb={showThinkingOrb || undefined}
>
```

Replace only the current identity header with:

```tsx
  <header className="tz-work-turn__identity">
    <MainAgentStatusAvatar
      showThinkingOrb={showThinkingOrb && view.presentation.isActive}
      phase={view.phase}
      identity={view.assistant.identity}
      activityLabel={view.presentation.activityLabel}
      className="tz-work-turn__avatar"
    />
    <span>{view.assistant.identity}</span>
    {view.presentation.statusLabel ? <small>{view.presentation.statusLabel}</small> : null}
  </header>
```

Leave all other WorkTurn children byte-for-byte unchanged. Do not change the page header, global launcher, `AgentAvatar`, `aria-busy`, `role="status"`, or `aria-live` behavior.

- [ ] **Step 4: Make `TurnStream` select exactly one orb owner**

Project once before rendering, scan from the end for the last active view, and pass ownership by stable WorkTurn key. Replace the current render loop with:

```tsx
const projectedTurns = thread.turns.map((turn) => ({ turn, view: projectWorkTurn(turn) }));
const orbOwnerKey = lastActiveWorkTurnKey(projectedTurns);

return (
  <div className="tz-turn-stream" aria-label="Conversation turns">
    {projectedTurns.map(({ turn, view }) => (
      <WorkTurnCard
        key={turnReactKey({
          threadId: thread.id,
          turnId: turn.id,
          clientMessageId: turn.client_message_id ?? `turn-${turn.id ?? "pending"}`,
        })}
        view={view}
        showThinkingOrb={view.key === orbOwnerKey}
        sourceStatus={turn.status}
        evidenceSummary={businessEvidence(turn)}
        technicalLog={technicalLog(turn)}
        deliverables={renderDeliverables({
          turn,
          thread,
          onArtifactAction,
          revisingArtifactId,
          actionPendingArtifactId,
          artifactRefreshKey,
          revisionArtifacts,
          sourceArtifactOverrides,
        })}
        businessActions={renderBusinessActions({
          turn,
          recoveryStatus: view.status,
          approvingToolCallId,
          approvalComment,
          onApprovalCommentChange,
          onApprove,
          resolvingInterruptId,
          onResolveInterrupt,
          onRestartTurn,
        })}
      />
    ))}
  </div>
);

function lastActiveWorkTurnKey(
  projectedTurns: Array<{ view: ReturnType<typeof projectWorkTurn> }>,
) {
  for (let index = projectedTurns.length - 1; index >= 0; index -= 1) {
    if (projectedTurns[index].view.presentation.isActive) {
      return projectedTurns[index].view.key;
    }
  }
  return null;
}
```

The reverse index loop must not reverse or mutate `thread.turns`.

- [ ] **Step 5: Replace the obsolete CSS pulse with the canvas footprint**

Delete the selector that applies `tz-agent-thinking` to `.tz-work-turn__avatar`, remove that selector from the reduced-motion block, and delete the now-unused `@keyframes tz-agent-thinking`. Add:

```css
.tz-main-agent-status-avatar {
  display: inline-grid;
  overflow: hidden;
  place-items: center;
}

.tz-main-agent-status-avatar canvas {
  display: block;
  width: 32px;
  height: 32px;
}
```

Keep `.tz-work-turn__avatar` at `32px × 32px` so phase changes and completion do not shift layout.

- [ ] **Step 6: Run the focused regression suite**

Run:

```powershell
cd frontend
npm.cmd test -- src/components/brain/workTurnOrbState.test.ts src/components/brain/MainAgentStatusAvatar.test.tsx src/components/brain/workTurnProjection.test.ts src/components/brain/WorkTurnCard.test.tsx src/components/brain/TurnStream.test.tsx src/pages/BrainHome.test.tsx
```

Expected: PASS; no test observes duplicate WorkTurns, duplicate live statuses, a header-avatar change, or more than one orb.

- [ ] **Step 7: Run full frontend quality and bundle gates**

Run:

```powershell
cd frontend
npm.cmd lint
npm.cmd test
npm.cmd build
npm.cmd run check:main-agent-bundle
```

Expected: all commands exit 0. Record the bundle check output in the implementation report; npm reports `thinking-orbs@0.2.0` at 47,214 unpacked bytes, but the actual built main-Agent chunk must remain within the repository's existing budget.

- [ ] **Step 8: Verify the real browser at required widths**

Start the existing Vite development command, open the operations-brain route with a running WorkTurn, and verify at 320px, 768px, 1024px, and 1440px:

1. Only the active WorkTurn avatar is animated.
2. The header operations-brain avatar and upper-right account/user avatar remain unchanged.
3. `understanding`, `reading_data`, `consulting_experts`, `quality_review`, and `composing_artifact` show distinct orb states with the existing Chinese status text.
4. Completion restores the static avatar in the same position without layout shift.
5. Reduced-motion renders a static representative orb frame.
6. Console and network panels contain no Canvas, accessibility, module-load, or hydration errors.

Capture one active-state and one completed-state screenshot for the review package; do not commit temporary screenshots unless the repository already tracks browser evidence for this feature.

- [ ] **Step 9: Commit Task 3**

```powershell
git add -- frontend/src/components/brain/TurnStream.tsx frontend/src/components/brain/TurnStream.test.tsx frontend/src/components/brain/WorkTurnCard.tsx frontend/src/components/brain/WorkTurnCard.test.tsx frontend/src/styles/brain-v2.css
git commit -m "feat: show one thinking orb in active work turn"
```

---

## Final Verification

After all three task reviews pass:

```powershell
git status --short
git diff --check HEAD~3..HEAD
cd frontend
npm.cmd lint
npm.cmd test
npm.cmd build
npm.cmd run check:main-agent-bundle
```

Expected:

- Only `docs/ideas/` and `docs/intent/` remain untracked.
- No whitespace errors.
- All frontend gates pass.
- The final branch review confirms that only active WorkTurn rendering changed and all global/header/account avatars are untouched.
