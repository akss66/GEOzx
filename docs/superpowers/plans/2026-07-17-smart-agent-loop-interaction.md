# Smart Agent Loop Interaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a server-driven, dynamically replanning operations Agent with inline strategy choices, composer-based permission confirmation, real expert handoffs, and resumable runtime state.

**Architecture:** The backend becomes the sole intent and next-step authority through a focused `brain_intelligence` service used by the existing LangGraph runtime. Durable decisions remain `Event` rows and existing BrainTask, invocation, tool-call, and acceptance ledgers remain intact. The frontend removes local casual routing, consumes structured runtime state, and splits the oversized Brain page into conversation, decision, and composer interaction components.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async, LangGraph, React 19, TypeScript, TanStack Query, Ant Design, Vitest, Testing Library, CSS motion with reduced-motion fallback.

## Global Constraints

- Desktop only for this delivery; mobile remains deferred.
- Current client, project, platform, and account context is mandatory for account-scoped work; no first-account fallback.
- High-risk tools, external platform actions, publishing, and formal writes remain human-confirmed.
- Strategy selection and permission approval are separate domain objects and separate UI states.
- Frontend must not fabricate Agent calls, stream events, or completion states.
- Business conversation must not display raw JSON, internal model names, or internal enum values.
- Reuse `BrainTask`, `Event`, `AgentInvocation`, `AgentToolCall`, and `DeliverableAcceptance`; do not add a workflow database or Celery/Redis.
- Do not copy Grok Build source code; only apply the reviewed architectural patterns.
- Treat `BrainTask.thread_id + Event` as the append-only parent session ledger.
- Treat each `AgentInvocation` as a one-level isolated child session with an explicit input package, tool whitelist and output contract.
- Project child-session lifecycle into the parent conversation in place; do not append duplicate status cards for every event.
- Permission grants are narrow and per action. Rejection comments become constraints for the resumed runtime.
- Preserve resumability: refresh, reconnect and a later user message must reconstruct the same task thread without frontend inference.

---

## File Structure

- Create `backend/app/orchestrator/brain_intelligence.py`: structured intent classification and per-round next-step decisions.
- Modify `backend/app/orchestrator/brain_planner.py`: remove silent fixed-expert fallback and delegate initial selection to intelligence decisions.
- Modify `backend/app/orchestrator/brain_runtime.py`: add classify, clarify, observe, decide-next and decision/permission pause routes.
- Modify `backend/app/orchestrator/brain_adapter.py`: execute only the expert stages selected for the current round.
- Modify `backend/app/schemas/brain.py`: public intent, runtime next-step, decision request and message request contracts.
- Modify `backend/app/api/brain.py`: server-driven message entry, decision selection/revision, pending-decision projection.
- Create `backend/tests/test_brain_intelligence.py`: isolated routing and next-step unit tests.
- Modify `backend/tests/test_brain_api.py`: runtime persistence, decision, permission, resume and no-default-experts integration tests.
- Modify `frontend/src/types.ts`: runtime intent and decision types.
- Modify `frontend/src/api/brain.ts`: message and decision endpoints.
- Create `frontend/src/components/brain/DecisionRequest.tsx`: conversation-native structured choices.
- Create `frontend/src/components/brain/BrainComposer.tsx`: normal, permission and revision composer state machine.
- Create `frontend/src/components/brain/ExpertTurn.tsx`: expert identity, streaming result and handoff presentation.
- Modify `frontend/src/pages/BrainHome.tsx`: remove client-side casual routing and compose the focused components.
- Modify `frontend/src/styles/brain-v2.css`: entrance, handoff, breathing and composer mode transitions.
- Modify `frontend/src/pages/BrainHome.test.tsx`: interaction and runtime integration tests.
- Create `frontend/src/components/brain/DecisionRequest.test.tsx` and `frontend/src/components/brain/BrainComposer.test.tsx`: focused component tests.
- Modify `tasks/current.md`: record implementation and verification status.

---

## Grok Build Reference Contract

The implementation is complete only when these architecture-level mappings are visible in code and tests:

1. **Persistent parent session:** all user messages, main-Agent messages, expert lifecycle events, decisions, permissions and compact summaries are append-only `Event` records under one `thread_id`.
2. **Isolated expert session:** an expert invocation receives only the current workspace scope, the task objective, compressed upstream observations, its prompt and its allowed tools. It returns a summary plus artifact references.
3. **One-level delegation:** only the main Agent dispatches experts in this phase. Expert tools cannot recursively dispatch other experts.
4. **In-place lifecycle projection:** the frontend derives one expert turn from start/progress/completion events and updates it in place. It does not render every lifecycle event as a separate card.
5. **Narrow permission resume:** approving or rejecting one tool call resumes the same runtime thread. A rejection comment is persisted and included in the next decision context.
6. **Durable recovery:** a reload or reconnect rebuilds the conversation from persisted events and pending domain objects, including strategy decisions and permission gates.

---

## Master Agent Control Plane Amendment

This amendment overrides any older step that starts a workflow from a precomputed expert list:

1. A formal workflow enters LangGraph through `decide_next`, never directly through `dispatch_round`.
2. `decide_next` reads an organization-scoped `Capability Registry` containing only enabled and authorized experts/tools. `OrchestrationPlan.steps` is not an expert allowlist.
3. `AgentInvocation` is created or executed only after the main Agent returns `dispatch_experts`; the frontend never previews an unconfirmed expert call.
4. Expert output returns to the parent as a compact conclusion plus artifact references, after which the main Agent decides the next action.
5. Experts cannot dispatch experts. Every handoff returns through the main Agent control plane.
6. Future MCP servers and tools register through the same capability contract and reuse permission gates, event ledgers and resume semantics.

Implementation order:

- Add tests proving that the initial expert selection does not come from the legacy plan and disabled experts are hidden from the main Agent.
- Add the dynamic capability catalog and move the LangGraph entry node to `decide_next`.
- Remove internal protocol and raw JSON from the business conversation while preserving full details in execution/audit views.

---

### Task 1: Server-Driven Intent Intelligence

**Files:**
- Create: `backend/app/orchestrator/brain_intelligence.py`
- Modify: `backend/app/schemas/brain.py`
- Test: `backend/tests/test_brain_intelligence.py`

**Interfaces:**
- Produces: `IntentKind`, `IntentDecision`, `RuntimeAction`, `RuntimeNextStep`.
- Produces: `BrainIntelligence.classify(session, org_id, message, has_account) -> IntentDecision`.
- Produces: `BrainIntelligence.decide_next(session, org_id, goal, observations, available_experts, round_index) -> RuntimeNextStep`.
- Consumed by: Tasks 2 and 3.

- [ ] **Step 1: Write failing classification tests**

```python
async def test_greeting_does_not_dispatch_experts(intelligence, session):
    decision = await intelligence.classify(session, 1, "你好", has_account=True)
    assert decision.intent == "conversation"
    assert decision.suggested_expert_codes == []


async def test_ambiguous_goal_asks_one_question(intelligence, session):
    decision = await intelligence.classify(session, 1, "帮我优化一下", has_account=True)
    assert decision.intent == "clarification"
    assert decision.clarifying_question
    assert decision.suggested_expert_codes == []
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `cd backend; uv run pytest tests/test_brain_intelligence.py -q`

Expected: FAIL because `brain_intelligence` and its contracts do not exist.

- [ ] **Step 3: Add the structured contracts and model parser**

```python
IntentKind = Literal["conversation", "clarification", "analysis", "workflow", "action"]
RuntimeAction = Literal[
    "respond",
    "ask_user",
    "dispatch_experts",
    "request_decision",
    "request_permission",
    "finish",
]


class IntentDecision(BaseModel):
    intent: IntentKind
    confidence: float = Field(ge=0, le=1)
    reason: str
    missing_field: str | None = None
    clarifying_question: str | None = None
    suggested_expert_codes: list[AgentCode] = Field(default_factory=list)
    requires_account_context: bool = False
```

Implement `BrainIntelligence` with strict JSON parsing, valid expert filtering, a greeting-safe deterministic guard, and an explicit `IntelligenceUnavailable` error. The failure path returns no experts and never selects positioning/content by default.

- [ ] **Step 4: Add next-step decision tests**

```python
async def test_next_step_can_finish_without_another_expert(intelligence, session):
    step = await intelligence.decide_next(
        session,
        1,
        "分析账号定位",
        [{"agent_code": "01-positioning", "summary": "定位已经明确"}],
        ["01-positioning", "06-operation"],
        1,
    )
    assert step.action == "finish"
    assert step.expert_codes == []
```

- [ ] **Step 5: Run unit tests**

Run: `cd backend; uv run pytest tests/test_brain_intelligence.py -q`

Expected: all intelligence tests PASS.

- [ ] **Step 6: Commit the independently testable routing service**

```bash
git add backend/app/orchestrator/brain_intelligence.py backend/app/schemas/brain.py backend/tests/test_brain_intelligence.py
git commit -m "feat: add server-driven brain intelligence"
```

### Task 2: Dynamic LangGraph Expert Loop

**Files:**
- Modify: `backend/app/orchestrator/brain_planner.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Modify: `backend/app/orchestrator/brain_adapter.py`
- Test: `backend/tests/test_brain_api.py`

**Interfaces:**
- Consumes: `IntentDecision` and `RuntimeNextStep` from Task 1.
- Produces: `run_brain_task_steps(session, task, agent_codes: list[str]) -> BrainTask`.
- Produces durable events: `brain.runtime.intent_classified`, `brain.runtime.next_step`, `brain.runtime.handoff`, `brain.runtime.clarification_requested`, and `brain.runtime.round_limit`.

- [ ] **Step 1: Add failing runtime behavior tests**

```python
@pytest.mark.asyncio
async def test_casual_message_creates_no_invocations(client, admin):
    response = await client.post("/brain/messages", headers=headers, json={"message": "你好"})
    assert response.status_code == 201
    runtime = response.json()
    assert runtime["intent"]["intent"] == "conversation"
    assert runtime["invocations"] == []


@pytest.mark.asyncio
async def test_runtime_observes_before_dispatching_next_expert(client, admin):
    runtime = await create_and_run_goal("分析账号定位并给出下周选题")
    event_types = [event["type"] for event in runtime["timeline"]]
    assert event_types.index("brain.runtime.subagent_completed") < event_types.index(
        "brain.runtime.next_step"
    )
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd backend; uv run pytest tests/test_brain_api.py -k "casual_message or observes_before" -q`

Expected: FAIL because `/brain/messages` and the observe/replan loop are absent.

- [ ] **Step 3: Add per-round pipeline execution**

```python
async def run_brain_task_steps(
    session: AsyncSession,
    task: BrainTask,
    agent_codes: list[str],
) -> BrainTask:
    content_item = await ensure_content_item(session, task)
    stages = {_AGENT_STAGE_BY_CODE[code] for code in agent_codes if code in _AGENT_STAGE_BY_CODE}
    await _engine.start(session, content_item.id, allowed_stages=stages)
    await sync_brain_task_from_pipeline(session, task)
    await session.commit()
    return task
```

- [ ] **Step 4: Replace the one-shot graph with a bounded loop**

Implement graph state fields `intent`, `round_index`, `selected_experts`, `observations`, `pending_decision_id`, and `pending_permissions`. Route:

```text
load_context -> classify_intent -> decide_next
decide_next -> ask_user | dispatch_experts | decision_gate | permission_gate | summarize
dispatch_experts -> observe_results -> decide_next
```

Persist each transition as an Event and stop after 6 rounds or 3 experts in one round.

- [ ] **Step 5: Remove the silent fixed expert fallback**

Change planner failure behavior to either return an empty safe plan for conversation/clarification or raise an explicit service error for a formal workflow. Delete the default branch that always selects positioning and content experts.

- [ ] **Step 6: Run runtime and existing pipeline tests**

Run: `cd backend; uv run pytest tests/test_brain_api.py tests/test_orchestrator.py -q`

Expected: PASS with no regression in existing tool permission and acceptance ledgers.

- [ ] **Step 7: Commit the dynamic runtime**

```bash
git add backend/app/orchestrator/brain_planner.py backend/app/orchestrator/brain_runtime.py backend/app/orchestrator/brain_adapter.py backend/tests/test_brain_api.py
git commit -m "feat: replan brain runtime after expert results"
```

### Task 3: Message and Strategy Decision APIs

**Files:**
- Modify: `backend/app/schemas/brain.py`
- Modify: `backend/app/api/brain.py`
- Modify: `backend/app/orchestrator/brain_runtime.py`
- Test: `backend/tests/test_brain_api.py`

**Interfaces:**
- Produces: `POST /brain/messages`.
- Produces: `POST /brain/tasks/{task_id}/decisions/{decision_id}/select`.
- Produces: `POST /brain/tasks/{task_id}/decisions/{decision_id}/revise`.
- Extends: `BrainRuntimeOut.intent` and `BrainRuntimeOut.pending_decisions`.

- [ ] **Step 1: Add failing decision lifecycle tests**

```python
@pytest.mark.asyncio
async def test_strategy_decision_is_persisted_and_resumed(client, admin):
    runtime = await create_runtime_with_decision_request()
    decision = runtime["pending_decisions"][0]
    selected = await client.post(
        f"/brain/tasks/{runtime['task']['id']}/decisions/{decision['id']}/select",
        headers=headers,
        json={"choice_id": decision["choices"][0]["id"]},
    )
    assert selected.status_code == 200
    assert selected.json()["pending_decisions"] == []
    assert any(e["type"] == "brain.runtime.decision_selected" for e in selected.json()["timeline"])
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `cd backend; uv run pytest tests/test_brain_api.py -k "decision_is_persisted" -q`

Expected: FAIL with route not found.

- [ ] **Step 3: Add request and response contracts**

```python
class BrainMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    task_id: int | None = None
    project_id: int | None = None
    account_id: int | None = None


class DecisionSelectionRequest(BaseModel):
    choice_id: str = Field(min_length=1, max_length=120)


class DecisionRevisionRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)
    request_new_options: bool = False
```

- [ ] **Step 4: Store decisions as durable runtime events**

Use `brain.runtime.decision_requested`, `brain.runtime.decision_selected`, and `brain.runtime.decision_revised` events. A decision is pending when its latest event is requested and no later selected/revised event closes the same ID.

- [ ] **Step 5: Resume the same thread after a decision**

The selection/revision endpoint validates organization and project scope, records the user decision, invokes `runtime_graph.resume_after_decision`, and returns the complete `BrainRuntimeOut`.

- [ ] **Step 6: Run API tests**

Run: `cd backend; uv run pytest tests/test_brain_api.py -q`

Expected: all brain lifecycle, permission, decision, and account-scope tests PASS.

- [ ] **Step 7: Commit decision APIs**

```bash
git add backend/app/schemas/brain.py backend/app/api/brain.py backend/app/orchestrator/brain_runtime.py backend/tests/test_brain_api.py
git commit -m "feat: add conversational strategy decisions"
```

### Task 4: Frontend Runtime Contracts and Server-Only Routing

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/brain.ts`
- Modify: `frontend/src/api/brain.test.ts`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/pages/BrainHome.test.tsx`

**Interfaces:**
- Consumes: Task 3 runtime and decision APIs.
- Produces: `sendBrainMessage`, `selectBrainDecision`, and `reviseBrainDecision`.

- [ ] **Step 1: Add failing API and page tests**

```tsx
it("sends a greeting to the backend without local casual routing", async () => {
  renderBrainHome();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "你好" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await waitFor(() => expect(sendBrainMessage).toHaveBeenCalledWith(expect.objectContaining({ message: "你好" })));
  expect(draftBrainTask).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd frontend; pnpm vitest run src/api/brain.test.ts src/pages/BrainHome.test.tsx`

Expected: FAIL because message and decision functions are absent and local casual routing still runs.

- [ ] **Step 3: Add TypeScript contracts and API clients**

```ts
export interface BrainIntentDecision {
  intent: "conversation" | "clarification" | "analysis" | "workflow" | "action";
  confidence: number;
  reason: string;
  clarifying_question: string | null;
  suggested_expert_codes: AgentCode[];
  requires_account_context: boolean;
}

export interface BrainDecisionRequest {
  id: string;
  title: string;
  summary: string;
  choices: BrainDecisionChoice[];
  allow_custom_input: boolean;
  status: "pending" | "selected" | "revised";
}
```

- [ ] **Step 4: Remove `casualTurns` and `isCasualPrompt`**

All composer submissions call `sendBrainMessage`. Formal runtime creation and continuation are chosen by the server response. Keep current task and selected account state unchanged.

- [ ] **Step 5: Run focused frontend tests**

Run: `cd frontend; pnpm vitest run src/api/brain.test.ts src/pages/BrainHome.test.tsx`

Expected: PASS.

- [ ] **Step 6: Commit frontend runtime contracts**

```bash
git add frontend/src/types.ts frontend/src/api/brain.ts frontend/src/api/brain.test.ts frontend/src/pages/BrainHome.tsx frontend/src/pages/BrainHome.test.tsx
git commit -m "feat: route brain conversation through backend"
```

### Task 5: Conversation Decisions and Composer Confirmation Mode

**Files:**
- Create: `frontend/src/components/brain/DecisionRequest.tsx`
- Create: `frontend/src/components/brain/DecisionRequest.test.tsx`
- Create: `frontend/src/components/brain/BrainComposer.tsx`
- Create: `frontend/src/components/brain/BrainComposer.test.tsx`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/styles/brain-v2.css`

**Interfaces:**
- `DecisionRequest(props: { decision; busy; onSelect; onRevise })`.
- `BrainComposer(props: { mode; permission; busy; value; onChange; onSubmit; onApprove; onReject; onRevise })`.

- [ ] **Step 1: Add failing decision component tests**

```tsx
it("lets the user select a recommended strategy without approving a tool", () => {
  render(<DecisionRequest decision={decision} onSelect={onSelect} onRevise={onRevise} />);
  fireEvent.click(screen.getByRole("radio", { name: /专业权威线/ }));
  fireEvent.click(screen.getByRole("button", { name: "采用这个方案" }));
  expect(onSelect).toHaveBeenCalledWith("authority");
});
```

- [ ] **Step 2: Add failing composer mode tests**

```tsx
it("morphs in place for permission and expands revision in the same composer", () => {
  const { rerender } = render(<BrainComposer mode="normal" {...baseProps} />);
  rerender(<BrainComposer mode="permission" permission={permission} {...baseProps} />);
  expect(screen.getByText("生成发布包")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "修改要求" }));
  expect(screen.getByRole("textbox", { name: "修改要求" })).toBeVisible();
});
```

- [ ] **Step 3: Run component tests and verify failure**

Run: `cd frontend; pnpm vitest run src/components/brain/DecisionRequest.test.tsx src/components/brain/BrainComposer.test.tsx`

Expected: FAIL because components do not exist.

- [ ] **Step 4: Implement conversation-native strategy choices**

Use semantic radio choices, a visible recommended marker, compact benefit/tradeoff rows, “采用这个方案”, “换一组方案”, and “输入自己的方向”. After selection, render the decision as a read-only conversation record.

- [ ] **Step 5: Implement the composer state machine**

Remove `InlineConfirmationPanel`. Keep one fixed composer footprint with `normal`, `permission`, and `revision` visual modes. The permission mode shows action/object/impact first; full details stay in execution details.

- [ ] **Step 6: Run component and page tests**

Run: `cd frontend; pnpm vitest run src/components/brain/DecisionRequest.test.tsx src/components/brain/BrainComposer.test.tsx src/pages/BrainHome.test.tsx`

Expected: PASS with no modal or absolutely positioned confirmation panel.

- [ ] **Step 7: Commit the approved B interactions**

```bash
git add frontend/src/components/brain/DecisionRequest.tsx frontend/src/components/brain/DecisionRequest.test.tsx frontend/src/components/brain/BrainComposer.tsx frontend/src/components/brain/BrainComposer.test.tsx frontend/src/pages/BrainHome.tsx frontend/src/styles/brain-v2.css
git commit -m "feat: add inline decisions and composer approvals"
```

### Task 6: Streaming Expert Identity and Handoff Motion

**Files:**
- Create: `frontend/src/components/brain/ExpertTurn.tsx`
- Create: `frontend/src/components/brain/ExpertTurn.test.tsx`
- Modify: `frontend/src/pages/BrainHome.tsx`
- Modify: `frontend/src/styles/brain-v2.css`
- Test: `frontend/src/pages/BrainHome.test.tsx`

**Interfaces:**
- Consumes actual runtime `message_start`, `message_delta`, `message_done`, `subagent_started`, `subagent_completed`, and `handoff` events.
- Produces `ExpertTurn` with `queued | running | streaming | done | failed` visual states.

- [ ] **Step 1: Add failing presentation tests**

```tsx
it("shows expert identity, one conclusion and expandable analysis", () => {
  render(<ExpertTurn invocation={invocation} liveText="" handoff="接下来交给账号运营专家" />);
  expect(screen.getByText("账号定位专家")).toBeVisible();
  expect(screen.getByText(/核心优势/)).toBeVisible();
  expect(screen.getByRole("button", { name: "展开完整分析" })).toBeVisible();
});
```

- [ ] **Step 2: Run test and verify failure**

Run: `cd frontend; pnpm vitest run src/components/brain/ExpertTurn.test.tsx`

Expected: FAIL because `ExpertTurn` does not exist.

- [ ] **Step 3: Implement expert turn presentation**

Parse structured expert output into business sections through the existing presentation adapters; display one core conclusion by default and hide raw JSON/model names. Render a compact identity mark and handoff copy.

- [ ] **Step 4: Add restrained state motion**

Add 180-240ms identity entrance, low-amplitude running status breathing, and short handoff transition. Keep layout dimensions stable and disable all nonessential transitions under `prefers-reduced-motion: reduce`.

- [ ] **Step 5: Run expert and page tests**

Run: `cd frontend; pnpm vitest run src/components/brain/ExpertTurn.test.tsx src/pages/BrainHome.test.tsx`

Expected: PASS.

- [ ] **Step 6: Commit expert motion and handoff**

```bash
git add frontend/src/components/brain/ExpertTurn.tsx frontend/src/components/brain/ExpertTurn.test.tsx frontend/src/pages/BrainHome.tsx frontend/src/styles/brain-v2.css
git commit -m "feat: animate expert handoffs from runtime events"
```

### Task 7: Full Verification and Desktop Acceptance

**Files:**
- Modify: `tasks/current.md`

**Interfaces:**
- Verifies all interfaces produced by Tasks 1-6.

- [ ] **Step 1: Run backend quality gates**

Run: `cd backend; uv run pytest tests/test_brain_intelligence.py tests/test_brain_api.py tests/test_orchestrator.py -q`

Expected: all selected tests PASS.

Run: `cd backend; uv run ruff check app/orchestrator/brain_intelligence.py app/orchestrator/brain_planner.py app/orchestrator/brain_runtime.py app/orchestrator/brain_adapter.py app/api/brain.py app/schemas/brain.py tests/test_brain_intelligence.py tests/test_brain_api.py`

Expected: no Ruff errors.

- [ ] **Step 2: Run frontend quality gates**

Run: `cd frontend; pnpm vitest run`

Expected: all frontend tests PASS.

Run: `cd frontend; pnpm build`

Expected: Vite production build succeeds.

Run: `cd frontend; pnpm lint`

Expected: zero ESLint errors; existing unrelated warnings may remain documented.

- [ ] **Step 3: Run desktop browser acceptance**

At 1440x900 and 1920x1080 verify:

1. “你好” returns only a streaming Main Agent message.
2. An ambiguous goal asks one question.
3. A clear account goal shows expert identity entrance and a real handoff.
4. Strategy choices remain inside the conversation.
5. Permission changes the bottom composer in place with no overlay panel.
6. Refresh restores pending decision or permission state.
7. No raw JSON, model names, console errors, overlap, horizontal scrolling, or fake expert events.

- [ ] **Step 4: Update execution status**

Mark only verified checklist items complete in `tasks/current.md` and record exact test counts and remaining risks.

- [ ] **Step 5: Commit verification record**

```bash
git add tasks/current.md
git commit -m "docs: record smart agent loop verification"
```
