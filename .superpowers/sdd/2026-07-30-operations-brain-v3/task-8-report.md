# Task 8 implementation report

## Delivered

- Rebuilt the Operations Brain chat projection around
  `ConversationThread.turns / ConversationTurn` as the only persisted chat
  source. Pending, running, completed, and failed states now render through the
  same `TurnArticle`.
- Added stable turn identity and pure projection helpers. Optimistic and server
  turns merge by `turn.id` or `client_message_id`, while React keys remain stable
  across server-id binding and no duplicate user message is appended.
- Removed BrainHome's legacy `ConversationStream`, `BrainRuntime`,
  `listBrainTasks`, `getBrainTaskRuntime`, global execution drawer, and active
  Task restoration path. Stale active-task storage is only cleared and can no
  longer reactivate legacy chat UI.
- Seeded the new-thread conversation query cache before activation. A fresh GET
  cannot overwrite its first optimistic turn, and wrong-account saved threads
  fail closed.
- Made streaming frame handling reliable:
  - transport event ids only checkpoint durable events;
  - ephemeral start/delta frames sharing one transport id are retained;
  - backend frames expose monotonic `stream_seq`;
  - done uses the next sequence after the final delta;
  - reducer rejects stale, mismatched-thread, mismatched-client, and
    mismatched-turn frames;
  - terminal content overrides live deltas and late deltas cannot reopen a turn;
  - reconnect replaces live overlay with the authoritative durable thread.
- Exposed authoritative `ConversationTurn.status` end to end and removed status
  inference from intent metadata.
- Kept default chat output business-facing: participating experts, overall state,
  quality-gate summary, and formal artifacts are visible. Route, Skill, Tool,
  Critic, Run, SkillRun, Invocation, and ToolCall identifiers remain folded under
  technical details.
- Preserved the exact persisted `Artifact.id` shared with the result center.
  `ArtifactCenter` and `TurnArtifact` were not modified.
- Preserved history, stop, approval, artifact actions, and result-center entry.
- Added sanitized execution summaries for tool-only and critic-only runs without
  exposing prompts, raw tool input/output, stack traces, or secrets.
- Added no Task 9 timing or model-call-count placeholders.

## Inherited half-change audit

- Kept and completed:
  - the Conversation status schema direction;
  - backend `stream_seq`;
  - durable-only EventSource de-duplication;
  - the turn projection/reducer and stable key direction;
  - TurnStream's authoritative status and folded technical details.
- Reworked:
  - BrainHome still mixed the removed renderer with legacy task state and calls,
    leaving undefined/error-prone state and two competing chat projections;
  - the optimistic append path was missing and server binding could duplicate or
    remount turns;
  - the previous page suite primarily asserted legacy runtime behavior, so it was
    replaced by focused V3 projection, cache, streaming, isolation, and retained
    control tests without skips.
- Fixed during RED/GREEN:
  - done initially reused the last delta sequence and was correctly discarded as
    stale; it now emits `N + 1`;
  - a new thread's first optimistic turn could be overwritten by an immediate
    empty GET;
  - a transient stale closure could discard first stream frames after switching
    threads;
  - a saved thread belonging to a different account could leak into the selected
    account context;
  - tool-only and critic-only executions disappeared when no AgentInvocation
    existed.

## TDD evidence

- RED: shared transport ids caused start/delta/done frame loss.
  GREEN: ephemeral frames bypass durable id de-duplication while durable replay
  remains idempotent.
- RED: done and the last delta had the same stream sequence.
  GREEN: terminal frames now advance the sequence and complete authoritatively.
- RED: optimistic/server turns duplicated or changed identity.
  GREEN: pure merge helpers bind server ids without changing the stable domain
  key.
- RED: late, cross-thread, wrong-client, and wrong-turn frames could affect live
  state.
  GREEN: the reducer enforces identity and monotonic ordering before applying a
  frame.
- RED: BrainHome could restore legacy task UI or overwrite a new-thread pending
  turn.
  GREEN: only conversation query cache drives rendering and new-thread cache is
  seeded synchronously.
- RED: tool-only and critic-only runs had no execution summary.
  GREEN: allowlisted summaries are returned independently of expert invocation.
- Review RED: a refreshed `waiting_permission` Turn retained its ToolCall ledger
  but Conversation history returned no approval projection.
  GREEN: history rebuilds an allowlisted typed approval from the Turn-scoped
  `waiting_approval` ToolCall; BrainHome restores the composer and Turn controls
  without returning ToolCall meta or raw payloads.
- Review RED: a refreshed `running` Turn left the composer enabled and exposed no
  usable stop target because local `pendingTurn` was empty.
  GREEN: `claimed`, `waiting_predecessor`, `queued`, `running`, and `retry_wait`
  are one authoritative active-status set, and stop uses the durable Turn's
  `client_message_id`.
- Review RED: a fast terminal stream followed by a late queued POST response kept
  the final text but regressed the Turn status to `queued`.
  GREEN: HTTP identity binding preserves any newer live/terminal overlay until
  the authoritative durable Conversation GET replaces the cached Turn.

## Verification

- Directed frontend Task 8 suite:
  `4 files passed, 52 tests passed`.
- Full frontend suite:
  `70 files passed, 332 tests passed`.
- Frontend TypeScript and production build:
  `tsc --noEmit && vite build` passed.
- Backend conversation API suite:
  `32 tests passed`.
- Ruff on all changed backend and backend test files: passed.
- `git diff --check`: passed.
- Legacy BrainHome runtime-path search: no
  `listBrainTasks`, `getBrainTaskRuntime`, `BrainRuntime`,
  `ConversationStream`, or active-task restoration references.

## Concerns / follow-up

- The backend suite still emits one pre-existing SQLAlchemy asynchronous
  connection-cancellation warning in a blocked-expert test; no assertion fails.
- The frontend suite still emits existing jsdom/React Router/Ant Design warnings,
  and Vite reports the existing large-chunk warning. Task 8 introduces no build
  failure.
- Task 9 should measure real perceived latency and model/tool spans before adding
  any duration or model-call-count UI.
