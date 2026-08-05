# 运营大脑 V4 Codex 式交互 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** 在不重写现有 Agent Runtime 的前提下，把运营大脑主对话收敛成唯一、实时、可操控、可恢复的 WorkTurn 交互，让用户像使用 Codex / Claude Code 一样持续看到当前工作、补充要求、停止任务并在原位置接收最终诊断建议。

**Architecture:** 保留现有 `ConversationThread`、`ConversationTurn`、`AgentRun`、SSE 和账号隔离；服务端持久化 Turn 与事件继续作为事实源。前端通过唯一 `projectWorkTurn()` selector 将快照和实时 overlay 投影为一个 `WorkTurnViewModel`，历史和运行中 Turn 使用同一组件树。运行中提交显式携带 `target_turn_id`，复用现有服务端 steering 分类与 lineage，不在前端复制业务判断。

**Tech Stack:** React 18、TypeScript、TanStack Query、Ant Design、Vitest、Testing Library、Playwright、FastAPI REST/SSE（现有接口，不新增数据库表）

## Global Constraints

- 当前产品只读取人工导入的抖音账号数据，诊断现状并给运营建议；不得新增自动发布、自动监测、效果归因或自动调优入口。
- `org_id + user_id + account_id + thread_id + turn_id` 继续作为隔离边界；切换账号必须取消旧流、清空临时投影和草稿。
- 一个用户输入只创建一个稳定 WorkTurn 根节点；Commentary、进度、最终回答和失败恢复均在原节点更新。
- 技术日志默认隐藏，不展示模型思维链；业务进度与可核验依据优先。
- 不保留两套消息 UI。删除确认无调用方的旧组件与旧样式，不引入兼容分支。
- 每个 Task 先写失败测试，再做最小实现；每个 Task 独立提交。

---

### Task 1: 接通运行中 Turn 控制契约

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/brain.ts`
- Test: `frontend/src/api/brain.test.ts`

**Step 1: Write the failing API contract test**

在 `frontend/src/api/brain.test.ts` 增加用例，要求运行中追加消息时把目标 Turn 原样提交：

```ts
it("sends the active target turn for server-side steering", async () => {
  mockApiPost.mockResolvedValue({ data: submission });

  await sendConversationTurn(21, {
    client_message_id: "follow-up-1",
    message: "补充：只分析近 30 天",
    target_turn_id: 502,
  });

  expect(mockApiPost).toHaveBeenCalledWith(
    "/brain/conversations/21/turns",
    expect.objectContaining({ target_turn_id: 502 }),
  );
});
```

**Step 2: Run the focused test and confirm it fails**

Run: `npm test -- src/api/brain.test.ts`

Expected: FAIL because `SendConversationTurnInput` and serialized body do not contain `target_turn_id`.

**Step 3: Implement the typed transport field**

Add to `SendConversationTurnInput`:

```ts
target_turn_id?: number | null;
```

Add to `sendConversationTurn()` request body:

```ts
target_turn_id: input.target_turn_id ?? null,
```

Do not add a client-side `steering_mode`; the server remains authoritative for supplement / replace / stop / independent classification.

**Step 4: Run the focused test**

Run: `npm test -- src/api/brain.test.ts`

Expected: PASS.

**Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/brain.ts frontend/src/api/brain.test.ts
git commit -m "feat: expose conversation turn steering target"
```

---

### Task 2: 建立唯一 WorkTurn 展示状态契约

**Files:**
- Create: `frontend/src/components/brain/workTurnPresentation.ts`
- Create: `frontend/src/components/brain/workTurnPresentation.test.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/brain/workTurnProjection.ts`
- Modify: `frontend/src/components/brain/workTurnProjection.test.ts`

**Step 1: Write failing presentation tests**

覆盖以下状态规则：

```ts
it.each([
  ["reading_data", "正在核对已导入的数据范围"],
  ["consulting_experts", "正在分析账号的主要问题"],
  ["quality_review", "正在核验结论与数据依据"],
  ["composing_artifact", "正在整理优先运营建议"],
])("maps %s to one business activity line", (phase, expected) => {
  expect(presentWorkTurnActivity({ phase, status: "running", steps: [] }))
    .toBe(expected);
});

it("never shows activity copy after a final answer", () => {
  const view = projectWorkTurn(completedTurnWithAnswer);
  expect(view.presentation.showActivity).toBe(false);
  expect(view.presentation.showFinal).toBe(true);
});

it("collapses completed progress but preserves failed unfinished steps", () => {
  expect(presentWorkTurnProgress(completedTurn).mode).toBe("summary");
  expect(presentWorkTurnProgress(failedTurn).mode).toBe("expanded");
});
```

**Step 2: Run tests and confirm failure**

Run: `npm test -- src/components/brain/workTurnPresentation.test.ts src/components/brain/workTurnProjection.test.ts`

Expected: FAIL because the presentation contract does not exist.

**Step 3: Add the presentation types**

Extend `WorkTurnViewModel` with one derived presentation object:

```ts
export interface WorkTurnPresentation {
  isActive: boolean;
  statusLabel: string | null;
  activityLabel: string | null;
  showActivity: boolean;
  showFinal: boolean;
  progressMode: "expanded" | "summary" | "hidden";
  processLabel: "查看分析过程" | "查看已完成过程";
}
```

The view model must contain this single projection rather than letting `WorkTurnCard`, `WorkTurnProgress`, and `BrainHome` independently infer status.

**Step 4: Implement pure presentation helpers**

In `workTurnPresentation.ts`, implement only deterministic functions:

```ts
export function presentWorkTurn(turn: {
  status: WorkTurnStatus;
  phase?: TurnPhase;
  persistedStatus: string;
  hasFinal: boolean;
  steps: WorkTurnStep[];
}): WorkTurnPresentation;
```

Rules:

- `hasFinal` or terminal completed status suppresses the live activity line.
- Active Turn has exactly one activity label.
- Completed steps summarize as `已完成 N 项检查`; active and failed Turn stay expanded.
- Failure preserves done/failed steps and uses `本次分析未完成`, never a generic refresh instruction.
- Runtime labels are limited to `read_data`, `check_completeness`, `specialist_work`, `quality_review`, `prepare_recommendation`; remove the user-visible `publish_content` mapping from V4.

**Step 5: Make `projectWorkTurn()` the only selector**

Compute `assistantText`, `steps`, `status`, and `presentation` once in `projectWorkTurn()`. Keep stream frame idempotence and terminal protection unchanged.

**Step 6: Run focused tests**

Run: `npm test -- src/components/brain/workTurnPresentation.test.ts src/components/brain/workTurnProjection.test.ts`

Expected: PASS.

**Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/brain/workTurnPresentation.ts frontend/src/components/brain/workTurnPresentation.test.ts frontend/src/components/brain/workTurnProjection.ts frontend/src/components/brain/workTurnProjection.test.ts
git commit -m "refactor: centralize work turn presentation state"
```

---

### Task 3: 将 WorkTurn 收敛为单一、原位更新的用户界面

**Files:**
- Modify: `frontend/src/components/brain/WorkTurnCard.tsx`
- Modify: `frontend/src/components/brain/WorkTurnCard.test.tsx`
- Modify: `frontend/src/components/brain/WorkTurnProgress.tsx`
- Modify: `frontend/src/components/brain/ProcessDisclosure.tsx`
- Modify: `frontend/src/components/brain/TurnStream.tsx`
- Modify: `frontend/src/components/brain/TurnStream.test.tsx`
- Modify: `frontend/src/styles/brain-v2.css`

**Step 1: Write failing component tests**

Add assertions for the user-visible contract:

```ts
it("keeps one assistant surface from activity through final answer", () => {
  const rendered = render(<WorkTurnCard view={workingView} />);
  const operator = screen.getByRole("region", { name: "运营大脑工作回合" });
  expect(within(operator).getAllByText("运营大脑")).toHaveLength(1);

  rendered.rerender(<WorkTurnCard view={completedView} />);
  expect(screen.getByRole("region", { name: "运营大脑工作回合" })).toBe(operator);
  expect(within(operator).queryByText(/正在/)).not.toBeInTheDocument();
  expect(within(operator).getByText(completedView.assistantText!)).toBeVisible();
});

it("shows one live status while the avatar breathes", () => {
  render(<WorkTurnCard view={workingView} />);
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(screen.queryByText("思考中")).not.toBeInTheDocument();
});

it("summarizes finished progress and expands it on demand", () => {
  render(<WorkTurnCard view={completedView} />);
  expect(screen.getByRole("button", { name: "已完成 4 项检查" })).toBeVisible();
  expect(screen.queryByText("读取账号数据")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "已完成 4 项检查" }));
  expect(screen.getByText("读取账号数据")).toBeVisible();
});
```

Also replace the unknown projection expectation: an unknown projection is ignored in the business layer and remains visible only as a sanitized technical detail; it must not tell the user to refresh.

**Step 2: Run focused tests and confirm failure**

Run: `npm test -- src/components/brain/WorkTurnCard.test.tsx src/components/brain/TurnStream.test.tsx`

Expected: FAIL on duplicate inference, always-expanded steps, old accessible names, and refresh copy.

**Step 3: Refactor the card around the presentation contract**

`WorkTurnCard` renders in this order:

1. stable user message;
2. one operator identity row with `AgentAvatar`;
3. one live activity line while active;
4. final answer in the same response node when complete;
5. progress summary/steps;
6. progressively disclosed process;
7. inline deliverables and user decisions.

Use `view.presentation.isActive` for both `aria-busy` and `data-thinking`. Delete `isActiveStatus()` and `STATUS_COPY` from the component.

**Step 4: Make progress collapse after completion**

Change `WorkTurnProgress` signature:

```ts
export function WorkTurnProgress({
  steps,
  mode,
}: {
  steps: WorkTurnStep[];
  mode: WorkTurnPresentation["progressMode"];
});
```

`expanded` shows steps, `summary` shows an accessible disclosure button, `hidden` returns null. Failed Turn stays expanded with completed and unfinished sections.

**Step 5: Tighten progressive disclosure**

Rename user-facing copy to `查看分析过程` and nested `技术详情`. `ProcessDisclosure` shows:

- data source / period / completeness summary;
- called experts and status;
- quality/evidence summary;
- technical IDs and timings only under the second disclosure.

Do not show raw model reasoning.

**Step 6: Remove refresh-required fallback**

In `TurnStream`, delete:

```tsx
{unknown ? <p>本轮有一条新进展，请刷新后查看。</p> : null}
```

Record unknown projection type only in `technicalLog()` as `未识别事件：<type>` after allowlisting the type string; the ongoing SSE remains responsible for recovery.

**Step 7: Apply production UI styles**

Update WorkTurn CSS to preserve the same grid geometry before and after completion, add reduced-motion handling for the breathing state, keep keyboard focus visible, and prevent progress expansion from changing user-message alignment.

**Step 8: Run focused tests**

Run: `npm test -- src/components/brain/WorkTurnCard.test.tsx src/components/brain/TurnStream.test.tsx`

Expected: PASS.

**Step 9: Commit**

```bash
git add frontend/src/components/brain/WorkTurnCard.tsx frontend/src/components/brain/WorkTurnCard.test.tsx frontend/src/components/brain/WorkTurnProgress.tsx frontend/src/components/brain/ProcessDisclosure.tsx frontend/src/components/brain/TurnStream.tsx frontend/src/components/brain/TurnStream.test.tsx frontend/src/styles/brain-v2.css
git commit -m "refactor: unify main agent work turn surface"
```

---

### Task 4: 让输入框在运行中可补充、排队和停止

**Files:**
- Modify: `frontend/src/components/brain/BrainComposer.tsx`
- Modify: `frontend/src/components/brain/BrainComposer.test.tsx`
- Modify: `frontend/src/styles/brain-v2.css`

**Step 1: Replace the old generating tests with the desired contract**

```ts
it("keeps text input and send available while a turn is running", () => {
  render(<BrainComposer value="补充：只看近30天" disabled={false} loading {...props} />);
  expect(screen.getByRole("textbox", { name: "运营大脑消息" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "补充或排队" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "停止当前任务" })).toBeVisible();
});

it("submits a running-turn follow-up with Enter", () => {
  const onSubmit = vi.fn();
  render(<BrainComposer value="补充限制" disabled={false} loading onSubmit={onSubmit} {...props} />);
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  expect(onSubmit).toHaveBeenCalledOnce();
});
```

Keep the permission-mode tests: a pending permission still morphs the composer into an explicit decision surface.

**Step 2: Run tests and confirm failure**

Run: `npm test -- src/components/brain/BrainComposer.test.tsx`

Expected: FAIL because loading currently disables the textarea and replaces send with stop.

**Step 3: Separate hard disable from running state**

Use these semantics:

```ts
const canType = !disabled && !pendingPermission;
const canSubmit = canType && !attachmentBusy && value.trim().length > 0;
```

- `disabled` means missing account, invalid scope, or request lock—not “Agent is running”.
- `loading` changes placeholder and submit label but does not disable typing.
- Render stop and send as separate buttons while loading.
- Loading placeholder: `继续补充要求，或提出下一项问题…`.
- Idle submit accessible name: `发送给运营大脑`.
- Running submit accessible name: `补充或排队`.
- Stop accessible name: `停止当前任务`.
- Capability launcher may remain disabled while loading to avoid starting a second explicit Skill accidentally; plain text steering stays available.

**Step 4: Preserve keyboard and attachment behavior**

Enter submits, Shift+Enter inserts a newline, IME composition does not submit, and uploading/removing attachments still blocks send. Stop never clears the draft.

**Step 5: Style both actions without enlarging the composer**

Keep the 56px idle geometry. While active, show the stop control adjacent to the send control and preserve mobile hit targets of at least 36px.

**Step 6: Run focused tests**

Run: `npm test -- src/components/brain/BrainComposer.test.tsx`

Expected: PASS.

**Step 7: Commit**

```bash
git add frontend/src/components/brain/BrainComposer.tsx frontend/src/components/brain/BrainComposer.test.tsx frontend/src/styles/brain-v2.css
git commit -m "feat: keep main agent composer interactive during work"
```

---

### Task 5: 在 BrainHome 中接通实时 steering、原位重试和稳定乐观 Turn

**Files:**
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `frontend/src/components/brain/TurnStream.tsx`
- Modify: `frontend/src/components/brain/TurnStream.test.tsx`
- Modify: `frontend/src/styles/brain-v2.css`

**Step 1: Write failing integration tests**

Add these page-level scenarios:

```ts
it("sends a follow-up against the latest active turn without disabling input", async () => {
  renderBrainHome({ activeTurn: runningTurn });
  fireEvent.change(screen.getByRole("textbox", { name: "运营大脑消息" }), {
    target: { value: "补充：不要生成长期策略" },
  });
  fireEvent.click(screen.getByRole("button", { name: "补充或排队" }));

  await waitFor(() => expect(sendConversationTurn).toHaveBeenCalledWith(
    81,
    expect.objectContaining({ target_turn_id: 502 }),
  ));
});

it("keeps the optimistic turn key and alignment when the server binds the turn", async () => {
  // Capture the same DOM root before and after mutation/SSE reconciliation.
});

it("renders retry inside the failed work turn and does not append a global regenerate action", () => {
  expect(within(failedTurnRoot).getByRole("button", { name: "重试未完成部分" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "重新生成" })).not.toBeInTheDocument();
});
```

Add an account switch case proving that a pending follow-up from account A cannot appear or restore its draft in account B.

**Step 2: Run tests and confirm failure**

Run: `npm test -- src/pages/BrainHome.test.tsx src/components/brain/TurnStream.test.tsx`

Expected: FAIL because `startWorkflow()` exits during generation, the payload omits `target_turn_id`, and regenerate is global.

**Step 3: Pass target lineage through `submitTurn()`**

Extend local mutation variables and `submitTurn()` arguments:

```ts
targetTurnId?: number | null;
```

Serialize it as `target_turn_id`. In `startWorkflow()`:

```ts
const targetTurnId = activeTurn?.id ?? null;
await submitTurn({
  content,
  requestedSkillCode: null,
  targetTurnId,
});
```

Remove `isGenerating` from the early-return guard. Keep `launcherRequestInFlight` as the double-submit lock. The server decides whether the follow-up is a supplement, replacement, stop, or independent queued Turn.

**Step 4: Keep optimistic Turns stable and scoped**

Use the existing `client_message_id` key from optimistic insert through server merge. Preserve current epoch/account/thread checks. A follow-up gets its own WorkTurn; if the server classifies it as steering, its steering notice and target lineage explain what happened without mutating the user’s message.

**Step 5: Move retry to the failed Turn**

Add `onRetryTurn(turn)` to `TurnStream`. Only terminal `failed`, `blocked`, or `cancelled` cards receive a recovery action. Retry reuses the source user message and attaches source `turn.id` where the existing server contract supports scoped revision; never render one global “重新生成” below the entire thread.

Label recovery based on status:

- failed: `重试未完成部分`;
- blocked: `查看如何继续`;
- cancelled: `重新开始本轮`.

**Step 6: Pass correct composer flags**

`BrainComposer.disabled` is no longer `isGenerating`; derive it from missing account / account not ready / active request lock. `loading={isGenerating}` only controls the active visual mode.

**Step 7: Run focused tests**

Run: `npm test -- src/pages/BrainHome.test.tsx src/components/brain/TurnStream.test.tsx`

Expected: PASS.

**Step 8: Commit**

```bash
git add frontend/src/pages/BrainHome.tsx frontend/src/pages/BrainHome.test.tsx frontend/src/components/brain/TurnStream.tsx frontend/src/components/brain/TurnStream.test.tsx frontend/src/styles/brain-v2.css
git commit -m "feat: wire live steering and inline turn recovery"
```

---

### Task 6: 删除被替代的旧消息 UI 和重复样式

**Files:**
- Delete: `frontend/src/components/brain/AgentOrchestration.tsx`
- Delete: `frontend/src/components/brain/AgentOrchestration.test.tsx`
- Delete: `frontend/src/components/brain/orchestrationAdapter.ts`
- Modify: `frontend/src/components/brain/WorkTurnCard.tsx`
- Modify: `frontend/src/index.css`
- Modify: `frontend/src/styles/brain-v2.css`
- Test: `frontend/src/components/brain/TurnStream.test.tsx`

**Step 1: Add a source-level regression test**

Add an assertion (or a small Node/Vitest source scan) proving production brain components no longer emit legacy classes:

```ts
expect(workTurnSource).not.toMatch(/dy-chat-(message|bubble|expert)/);
expect(turnStreamSource).not.toMatch(/dy-chat-(message|bubble|expert)/);
```

Keep a positive assertion for the single `.tz-work-turn` root.

**Step 2: Run the test and confirm failure**

Run: `npm test -- src/components/brain/TurnStream.test.tsx`

Expected: FAIL because the avatar still uses `dy-chat-avatar` and legacy CSS remains.

**Step 3: Remove confirmed dead components**

`rg` shows `AgentOrchestration` and `orchestrationAdapter` have no production caller. Delete them and their isolated preview test. Do not touch runtime orchestration services.

**Step 4: Rename the remaining avatar class**

Replace `dy-chat-avatar` with `tz-work-turn__avatar` in the WorkTurn component and styles.

**Step 5: Delete legacy CSS blocks**

Remove unused `.dy-chat-message*`, `.dy-chat-bubble*`, `.dy-chat-expert*`, and duplicate thinking selectors from `frontend/src/index.css` and `brain-v2.css`. Preserve only selectors with live production references verified by:

Run: `rg "dy-chat-" frontend/src --glob "*.tsx" --glob "*.ts" --glob "*.css"`

Expected: no production output. Test-only negative assertions may remain.

**Step 6: Run frontend tests and build**

Run:

```bash
npm test -- src/components/brain/TurnStream.test.tsx src/components/brain/WorkTurnCard.test.tsx
npm run build
```

Expected: PASS; TypeScript has no deleted imports.

**Step 7: Commit**

```bash
git add -A frontend/src/components/brain frontend/src/index.css frontend/src/styles/brain-v2.css
git commit -m "refactor: remove legacy main agent chat ui"
```

---

### Task 7: 验证断线恢复、账号隔离、无重复渲染和交互性能

**Files:**
- Modify: `frontend/src/hooks/useConversationTurnEvents.test.tsx`
- Modify: `frontend/src/pages/BrainHome.test.tsx`
- Modify: `frontend/e2e/main-agent-v2.spec.ts`
- Modify: `frontend/scripts/check-main-agent-bundle.mjs` only if the intentional component split changes its allowlist
- Modify: `docs/superpowers/specs/2026-08-05-main-agent-v4-codex-interaction-design.md`

**Step 1: Add recovery and isolation tests**

Cover all acceptance paths:

- snapshot renders first, SSE resumes from `after_id`;
- duplicate `(turn_id, sequence)` is ignored;
- a sequence gap invokes snapshot recovery without a manual refresh message;
- refresh retains one WorkTurn and its partial assistant text;
- switching account aborts old stream and does not leak its Turn, draft, events, or artifacts;
- terminal event cannot regress to running;
- failed Turn retains completed steps and retries only from its inline action.

**Step 2: Add browser-level user journeys**

In Playwright, exercise:

1. send “分析这个账号最近30天的数据”；
2. assert optimistic user message appears immediately in final alignment;
3. assert one breathing avatar and one activity line;
4. while running, send “补充：不要生成长期策略”；
5. assert an in-turn steering notice appears;
6. assert streamed text grows in the same assistant node;
7. reload during work and assert the same Turn ID restores;
8. stop and assert completed steps remain;
9. switch account and assert previous content disappears;
10. expand analysis process, then technical details.

Mock only the network boundaries necessary to deterministically emit snapshot and SSE frames. Use the real component tree.

**Step 3: Run the complete verification suite**

Run:

```bash
cd frontend
npm test
npm run lint
npm run build
npm run check:main-agent-bundle
npm run perf:check
npm run test:e2e -- main-agent-v2.spec.ts
```

Expected: all PASS. Record actual build and test output before claiming completion.

**Step 4: Run the relevant backend contract tests**

The backend is unchanged, but verify the steering and SSE contracts used by the frontend:

```bash
cd backend
uv run pytest tests/test_conversation_api.py tests/test_turn_steering.py tests/test_turn_events_api.py tests/test_main_agent_worker_contract.py -q
```

Expected: PASS.

**Step 5: Update design status and acceptance evidence**

Change the spec status from “等待产品负责人审阅本文” to “已实现，等待线上验收”. Add a short verification table containing the exact commands and results. Do not mark production deployment complete unless an actual deployment and online smoke test occur.

**Step 6: Commit**

```bash
git add frontend/src/hooks/useConversationTurnEvents.test.tsx frontend/src/pages/BrainHome.test.tsx frontend/e2e/main-agent-v2.spec.ts frontend/scripts/check-main-agent-bundle.mjs docs/superpowers/specs/2026-08-05-main-agent-v4-codex-interaction-design.md
git commit -m "test: verify codex-style main agent interaction"
```

---

## Final Review Gate

Before merging or deploying:

1. Use `superpowers:requesting-code-review` for correctness, state consistency, account isolation, accessibility, and regression review.
2. Use `superpowers:verification-before-completion` and rerun the exact full commands above from a clean process.
3. Manually inspect the main page at desktop and mobile widths. Confirm there is one UI path, one status line, one avatar, stable alignment, enabled running composer, inline stop/retry, and no refresh-required copy.
4. Verify `git status --short` contains only the intended V4 files plus the user-owned pre-existing `docs/ideas/` and `docs/intent/` directories.
5. Production rollout is a separate authorization gate: build artifact, deploy, online smoke test, and rollback confirmation must happen only after local verification passes.
