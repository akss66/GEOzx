# ADR 0003: Main Agent V2 Conversation And Skill Runtime

- Status: Accepted
- Date: 2026-07-28
- Decision owner: Product and engineering

## Context

The operations brain currently reuses one `BrainTask` as a long-lived
conversation, the current user request, an Agent execution, a durable business
task, and a result container. Later messages can overwrite the task brief and
reuse strategies created for an earlier goal. The frontend then projects
task-wide strategies, deliverables, approvals, and scores after the latest
message.

This makes ordinary data questions enter the full strategy graph, causes old
artifacts to move through the conversation, and leaves the user unable to tell
what was delivered or where it belongs.

The product also needs one main Agent to cover the full account-operations loop
without exposing orchestration complexity. Users must be able to enter from any
stage, use natural language or a discoverable capability launcher, see which
specialists participated, and receive durable artifacts in both the source
conversation and an account artifact center.

ADR 0001 remains the production runtime and memory decision. ADR 0002 remains
the unified AgentKernel, specialist isolation, and tool-boundary decision.

## Decision

### Separate conversation, execution, and business work

Adopt the following ownership hierarchy:

```text
ConversationThread
  -> ConversationTurn
      -> AgentRun
          -> SkillRun
              -> ExpertInvocation
              -> ToolCall
              -> Critic
          -> OperationTask (only when durable business work is required)
          -> Artifact / Approval / Observation
```

- `ConversationThread` owns long-lived dialogue and active account context.
- Every user message creates one immutable `ConversationTurn`.
- Every Turn has an idempotent `AgentRun`.
- A `SkillRun` records one versioned business capability execution.
- An `OperationTask` is created only for durable, cross-turn, risky, or
  observation-based work.
- Artifacts and approvals must retain their source Thread, Turn, and Run.

`BrainTask` remains during migration as the compatibility carrier for
`OperationTask`. It no longer defines conversation ownership.

### Make Skill the business capability boundary

The main Agent chooses and combines Skills. A Skill defines typed input,
execution graph, allowed specialists, tools, approval policy, output contract,
retry policy, and success criteria.

A Skill is not an alias for a specialist:

- one Skill may call multiple specialists and tools;
- one specialist may serve multiple Skills;
- a direct specialist request creates a single-specialist SkillRun;
- all paths use the same Runtime, permission, audit, and quality boundaries.

### Route automatically

The main Agent automatically distinguishes:

- direct conversation;
- deterministic data query;
- bounded Skill execution;
- durable OperationTask;
- high-risk action requiring approval.

The user may explicitly override with “discussion only” or “create a formal
task.” Ordinary conversation and data queries do not enter the full strategy
graph.

### Anchor artifacts to their source

Every formal artifact is rendered under its originating Turn and is also
discoverable in the account artifact center. Both surfaces reference the same
artifact identity and version chain. Later messages cannot move or implicitly
regenerate historical artifacts.

### Add a business capability launcher

The composer receives a left-side “＋” launcher. It lists user-facing
capabilities such as one-click account inspection, data review, topic planning,
script generation, and publishing preparation. Entries come from the Skill
Registry and submit a structured `requested_skill_code`; they do not work by
concatenating hidden prompt text.

Execution shows user-readable stages and specialist names by default. Evidence
and quality details are expandable. Raw technical logs are a deeper,
non-default view.

### Preserve approval boundaries

Reading authorized data, invoking specialists, and producing drafts may run
automatically. Publishing, deletion, paid promotion, authorization changes, and
other irreversible external actions require explicit user approval.

## Alternatives Considered

### Continue using BrainTask as the conversation container

Rejected. It cannot reliably distinguish message ownership, execution
idempotency, durable business state, or artifact provenance.

### Expose every specialist directly as a Skill

Rejected as the primary model. It makes the main Agent repeatedly implement
multi-specialist orchestration and prevents composite capabilities such as
account inspection from having a stable contract.

### Hard-code one workflow per product feature

Rejected. Fixed workflows are initially predictable but cannot support entry
from any operations stage or continued extension through new specialists,
tools, MCP servers, and platforms.

### Require the user to choose discussion or task mode before every message

Rejected as the default interaction. It adds friction and exposes an internal
system decision. Explicit override remains available when the user wants it.

## Consequences

Positive:

- ordinary questions no longer create or reuse strategy tasks;
- every artifact has stable provenance and a predictable location;
- the main Agent can cover the full operations loop without becoming one giant
  prompt or one fixed workflow;
- Skills become discoverable product capabilities and versioned runtime
  contracts;
- specialist isolation, Tool permissions, approvals, and audit remain intact;
- migration can be incremental and backward compatible.

Costs:

- new Thread, Turn, SkillRun, and artifact-projection contracts must be added;
- existing task-wide queries and frontend rendering need compatibility layers;
- status semantics must be separated across Turn, SkillRun, OperationTask, and
  Artifact;
- Skill definitions and quality contracts require ongoing governance.

## Migration

1. Add nullable Thread/Turn ownership to the existing runtime.
2. Add conversation APIs while retaining `/brain/tasks/*`.
3. Split intent routing before changing specialist behavior.
4. Add SkillRun and versioned Skill Registry contracts.
5. Link artifacts and approvals to source Turn/Run.
6. Project the frontend by Turn and add the account artifact center.
7. Add the composer capability launcher.
8. Remove compatibility fallbacks only after production telemetry confirms that
   no active consumer depends on task-wide conversation behavior.

## Related Documents

- `docs/superpowers/specs/2026-07-28-main-agent-v2-operating-loop-design.md`
- `docs/adr/0001-production-agent-runtime-and-double-memory.md`
- `docs/adr/0002-pi-inspired-unified-agent-kernel.md`
