import type { Edge, Node } from "@xyflow/react";

import type {
  AgentInvocation,
  AgentToolCall,
  DeliverableAcceptance,
  OrchestrationPlanStep,
} from "../../types";

export type TraceMode = "simple" | "full";

export type TraceNodeData = {
  kind: "agent" | "tool" | "gate" | "deliverable" | "rerun";
  label: string;
  status: string;
  meta: string;
  invocation?: AgentInvocation;
  toolCall?: AgentToolCall;
  acceptance?: DeliverableAcceptance;
};

export type TraceNode = Node<TraceNodeData>;

type BuildTraceOptions = {
  steps: OrchestrationPlanStep[];
  invocations: AgentInvocation[];
  toolCalls?: AgentToolCall[];
  acceptances?: DeliverableAcceptance[];
  qualityGates?: string[];
  currentFocus?: string;
  mode?: TraceMode;
};

export function buildAgentTraceGraph(
  optionsOrSteps: BuildTraceOptions | OrchestrationPlanStep[],
  legacyInvocations: AgentInvocation[] = [],
): { nodes: TraceNode[]; edges: Edge[] } {
  const options = Array.isArray(optionsOrSteps)
    ? { steps: optionsOrSteps, invocations: legacyInvocations }
    : optionsOrSteps;
  const {
    steps,
    invocations,
    toolCalls = [],
    acceptances = [],
    qualityGates = [],
    currentFocus = "",
    mode = "simple",
  } = options;
  const invocationByAgent = new Map(invocations.map((row) => [row.agent_code, row]));
  const ordered = steps.length > 0 ? steps : invocations.map(invocationToStep);

  const nodes = ordered.map<TraceNode>((step, index) => {
    const invocation = invocationByAgent.get(step.agent_code);
    const status = invocation?.status ?? step.status;
    return {
      id: step.id || step.agent_code,
      type: "default",
      draggable: true,
      position: { x: index * 220, y: index % 2 === 0 ? 20 : 116 },
      data: {
        kind: "agent",
        label: step.agent_name,
        status,
        meta: invocation
          ? `${invocation.model} · $${Number(invocation.cost).toFixed(3)}`
          : step.expected_output,
        invocation,
      },
      style: {
        width: 188,
        borderRadius: 8,
        border: `1px solid ${status === "blocked" ? "var(--dy-warning)" : "var(--dy-border)"}`,
        background: "var(--dy-elevated)",
        color: "var(--dy-text)",
        fontSize: 12,
      },
    };
  });

  const edges = nodes.slice(1).map<Edge>((node, index) => ({
    id: `${nodes[index].id}-${node.id}`,
    source: nodes[index].id,
    target: node.id,
    animated: node.data.status === "running",
    style: { stroke: "var(--dy-border)" },
  }));

  if (mode === "full") {
    appendQualityGateNodes(nodes, edges, ordered, qualityGates, currentFocus);
    appendToolNodes(nodes, edges, ordered, toolCalls);
    appendDeliverableNodes(nodes, edges, ordered, acceptances);
  }

  return { nodes, edges };
}

function appendQualityGateNodes(
  nodes: TraceNode[],
  edges: Edge[],
  steps: OrchestrationPlanStep[],
  qualityGates: string[],
  currentFocus: string,
) {
  qualityGates.forEach((gate, index) => {
    const source = nodes[Math.min(index, Math.max(nodes.length - 1, 0))];
    if (!source) return;
    const isFocused = currentFocus.includes(gate);
    const id = `gate-${index}`;
    nodes.push({
      id,
      type: "default",
      draggable: true,
      position: { x: source.position.x + 110, y: -84 },
      data: {
        kind: "gate",
        label: gate,
        status: isFocused ? "pending" : "configured",
        meta: isFocused ? "当前等待人工确认" : "质量门已配置",
      },
      style: nodeStyle(isFocused ? "blocked" : "planned", 160),
    });
    edges.push({
      id: `${source.id}-${id}`,
      source: source.id,
      target: id,
      animated: isFocused,
      style: { stroke: "var(--dy-warning)" },
    });
    const nextStep = steps[index + 1];
    const nextNode = nextStep ? nodes.find((node) => node.id === nextStep.id) : null;
    if (nextNode) {
      edges.push({
        id: `${id}-${nextNode.id}`,
        source: id,
        target: nextNode.id,
        style: { stroke: "var(--dy-border)" },
      });
    }
  });
}

function appendToolNodes(
  nodes: TraceNode[],
  edges: Edge[],
  steps: OrchestrationPlanStep[],
  toolCalls: AgentToolCall[],
) {
  const perAgent = new Map<string, AgentToolCall[]>();
  toolCalls.forEach((toolCall) => {
    const key = toolCall.agent_code ?? "";
    perAgent.set(key, [...(perAgent.get(key) ?? []), toolCall]);
  });

  steps.forEach((step) => {
    const sourceNode = nodes.find((node) => node.id === step.id);
    if (!sourceNode) return;
    const calls = perAgent.get(step.agent_code) ?? [];
    calls.forEach((toolCall, index) => {
      const id = `tool-${toolCall.id}`;
      nodes.push({
        id,
        type: "default",
        draggable: true,
        position: {
          x: sourceNode.position.x + 24 + index * 28,
          y: sourceNode.position.y + 92 + index * 54,
        },
        data: {
          kind: "tool",
          label: toolCall.tool_name,
          status: toolCall.status,
          meta:
            toolCall.permission_mode === "confirm"
              ? "requires human confirmation"
              : toolCall.tool_code,
          toolCall,
        },
        style: nodeStyle(toolCall.status === "waiting_approval" ? "blocked" : toolCall.status, 172),
      });
      edges.push({
        id: `${sourceNode.id}-${id}`,
        source: sourceNode.id,
        target: id,
        animated: toolCall.status === "running" || toolCall.status === "waiting_approval",
        style: {
          stroke:
            toolCall.status === "waiting_approval"
              ? "var(--dy-warning)"
              : "var(--dy-border)",
        },
      });
    });
  });
}

function appendDeliverableNodes(
  nodes: TraceNode[],
  edges: Edge[],
  steps: OrchestrationPlanStep[],
  acceptances: DeliverableAcceptance[],
) {
  acceptances.forEach((acceptance, index) => {
    const sourceStep = steps.find((step) => step.agent_code === acceptance.agent_code);
    const sourceNode = nodes.find((node) => node.id === sourceStep?.id) ?? nodes[index];
    if (!sourceNode) return;

    const deliverableId = `deliverable-${acceptance.id}`;
    nodes.push({
      id: deliverableId,
      type: "default",
      draggable: true,
      position: { x: sourceNode.position.x, y: 220 + (index % 2) * 80 },
      data: {
        kind: "deliverable",
        label: acceptance.title,
        status: acceptance.status,
        meta: `${acceptance.agent_name} · v${acceptance.version}`,
        acceptance,
      },
      style: nodeStyle(
        acceptance.status === "rerun_requested" ? "blocked" : acceptance.status,
        190,
      ),
    });
    edges.push({
      id: `${sourceNode.id}-${deliverableId}`,
      source: sourceNode.id,
      target: deliverableId,
      style: { stroke: "var(--dy-border)" },
    });

    if (acceptance.status === "rerun_requested") {
      const rerunId = `rerun-${acceptance.id}`;
      nodes.push({
        id: rerunId,
        type: "default",
        draggable: true,
        position: { x: sourceNode.position.x + 44, y: 340 + (index % 2) * 70 },
        data: {
          kind: "rerun",
          label: "返工重跑",
          status: acceptance.rerun_scope ?? "current_agent",
          meta: acceptance.reviewer_note ?? "等待运营大脑重新调度",
          acceptance,
        },
        style: nodeStyle("blocked", 150),
      });
      edges.push(
        {
          id: `${deliverableId}-${rerunId}`,
          source: deliverableId,
          target: rerunId,
          animated: true,
          style: { stroke: "var(--dy-warning)" },
        },
        {
          id: `${rerunId}-${sourceNode.id}`,
          source: rerunId,
          target: sourceNode.id,
          animated: true,
          style: { stroke: "var(--dy-warning)", strokeDasharray: "5 5" },
        },
      );
    }
  });
}

function nodeStyle(status: string, width = 188) {
  return {
    width,
    borderRadius: 8,
    border: `1px solid ${status === "blocked" ? "var(--dy-warning)" : "var(--dy-border)"}`,
    background: "var(--dy-elevated)",
    color: "var(--dy-text)",
    fontSize: 12,
  };
}

function invocationToStep(invocation: AgentInvocation): OrchestrationPlanStep {
  return {
    id: `invocation-${invocation.id}`,
    agent_code: invocation.agent_code,
    agent_name: invocation.agent_name,
    phase: "直接调用",
    intent: invocation.input_summary,
    status: invocation.status === "queued" ? "planned" : invocation.status,
    depends_on: invocation.upstream.map(String),
    expected_output: invocation.output_summary,
    risk_level: "low",
  };
}
