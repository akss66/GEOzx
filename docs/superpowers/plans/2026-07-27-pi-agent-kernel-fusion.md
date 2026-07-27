# Pi-Inspired Agent Kernel Fusion Plan

## Goal

Unify the main Agent and specialist Agents behind one production control loop
while preserving the current FastAPI, LangGraph, ARQ, PostgreSQL, permission,
audit, and frontend contracts.

## Phase 1: Kernel contracts and policy boundary

- [ ] Add typed actor, action, lifecycle event, state, and policy contracts.
- [ ] Define separate main-Agent and specialist policies.
- [ ] Reject specialist-to-specialist dispatch in code.
- [ ] Enforce tool allowlists and round/tool budgets in code.
- [ ] Validate every main runtime decision through the main policy.
- [ ] Add unit tests for all allowed and denied actions.

## Phase 2: Bounded specialist loop

- [ ] Add specialist decision schema: call tools, complete, or return blocked.
- [ ] Run specialist turns through `AgentKernel`.
- [ ] Feed tool observations back into the specialist context.
- [ ] Preserve typed deliverable validation and the existing repair attempt.
- [ ] Record kernel lifecycle events under the existing invocation.
- [ ] Keep specialists unable to ask the user or dispatch specialists.

## Phase 3: Main runtime convergence

- [ ] Move next-step execution behind the same kernel action executor.
- [ ] Support steering and follow-up queues without creating duplicate tasks.
- [ ] Unify stop, retry, resume, and cancellation checks at turn boundaries.
- [ ] Remove the legacy fixed expert pipeline after compatibility tests pass.
- [ ] Guarantee stable event ordering and idempotent replay after reconnect.

## Phase 4: Tool and MCP convergence

- [ ] Register MCP servers and tools as capability records.
- [ ] Route local and MCP calls through the same `ToolExecutor`.
- [ ] Apply tenant scope, risk, permission, timeout, retry, and redaction policy.
- [ ] Add MCP health, degraded mode, and audit events.
- [ ] Keep external writes and destructive actions behind human approval.

## Phase 5: Two-layer memory and knowledge loop

- [ ] Enable automatic runtime compaction at safe turn boundaries.
- [ ] Extract evidence-backed knowledge suggestions after accepted outcomes.
- [ ] Add fingerprint deduplication and conflict/supersession handling.
- [ ] Require review before writing active long-term knowledge.
- [ ] Record citations for every retrieved knowledge item.

## Phase 6: Production hardening

- [ ] Add run, turn, model, specialist, tool, queue, and approval metrics.
- [ ] Add timeout, cancellation, retry, dead-letter, and checkpoint recovery tests.
- [ ] Add prompt and policy regression evaluations.
- [ ] Enable for one administrator and one Douyin account before wider rollout.
- [ ] Publish rollback and incident runbooks.

## Acceptance criteria

1. The main Agent remains the only controller and dynamically selects specialists.
2. A specialist can iterate with allowlisted tools but cannot dispatch a specialist.
3. All actions are scoped, budgeted, audited, interruptible, and recoverable.
4. User-visible events remain ordered across reconnects and retries.
5. Accepted outcomes can create reviewed, evidence-backed knowledge suggestions.
