# ADR 0002: Pi-Inspired Unified Agent Kernel

- Status: Accepted
- Date: 2026-07-27
- Decision owner: Product and engineering

## Context

The current runtime already has durable task ledgers, LangGraph orchestration,
specialist invocations, tool permission gates, streaming events, and two-layer
memory foundations. Its control logic is still split across the main runtime,
the specialist harness, and one-shot specialist model calls.

This split causes four product problems:

1. Main-Agent and specialist behavior do not share one action and event contract.
2. Specialists cannot perform a bounded observe-tool-reason loop.
3. Steering, follow-up, stop, resume, and tool lifecycle behavior are inconsistent.
4. Adding MCP tools would otherwise create another execution path outside the
   existing permission and audit boundary.

The Pi project demonstrates a small, explicit Agent loop with typed state,
streaming lifecycle events, tool hooks, steering, follow-up messages, abort, and
continuation. We adopt these semantics without embedding Pi's coding tools or
replacing the existing Python runtime.

## Decision

Build one Python-native `AgentKernel` used by both the main Agent and specialist
Agents. Keep FastAPI, LangGraph, PostgreSQL, ARQ, and the current business
ledgers as the source of truth.

### Main-Agent policy

The main Agent is the only global controller. It may:

- respond or ask the user;
- dispatch one or more specialists;
- call registered tools and future MCP tools;
- request a decision or permission;
- revise the plan after new observations;
- stop, resume, or finish the run.

The main Agent does not replace specialist work with its own domain answer when
the intent requires a registered specialist.

### Specialist policy

A specialist receives an isolated input packet and may:

- reason over the assigned purpose and evidence;
- call only tools in its explicit allowlist;
- observe tool results and revise its answer;
- produce one typed deliverable or return a structured blocked result.

A specialist may not:

- dispatch another specialist;
- communicate with the user directly;
- bypass the main Agent or a human permission gate;
- access a client, project, account, database, network, or MCP tool outside its
  assigned scope;
- continue after its round, tool, time, token, or cost budget is exhausted.

### Unified lifecycle

Every kernel run emits the same ordered lifecycle:

`agent_start -> turn_start -> decision -> action/tool events -> turn_end -> agent_end`

Tool calls additionally emit:

`tool_start -> tool_update* -> tool_end`

Events are projected to the existing `Event`, `AgentInvocation`, and
`AgentToolCall` ledgers. The kernel does not create a second business ledger.

### Tools and MCP

Local tools and MCP tools use the same capability registry and `ToolExecutor`.
The model never receives direct database, network, filesystem, shell, credential,
or platform access. Policy and scope checks are code boundaries, not prompt
instructions.

### Memory

- Runtime memory stores the current thread's goals, decisions, observations,
  summaries, pending work, and workspace scope.
- Long-term knowledge is extracted into a suggestion with evidence, deduplicated,
  conflict-checked, and reviewed before it becomes active knowledge.

Steering messages affect the next turn. Follow-up messages are processed after
the current bounded action completes. Neither silently creates a second run.

## Alternatives

### Node sidecar using `pi-agent-core`

Rejected for the current phase. It would introduce two runtimes and duplicate
checkpoint, tenancy, authorization, event ordering, and deployment concerns.

### Full TypeScript rewrite

Rejected. It would discard working platform integration, audit, data, and
workflow infrastructure without improving near-term product usefulness.

## Consequences

- Main and specialist Agents gain one testable control contract.
- Specialist autonomy becomes useful but bounded.
- MCP can be added without weakening authorization.
- Migration is incremental: the existing LangGraph remains live while its
  decisions and specialist execution move behind the kernel.
- Existing one-shot specialist calls remain compatible until each specialist is
  migrated to the bounded loop.
