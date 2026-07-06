import { describe, expect, it } from "vitest";

import { buildAgentTraceGraph } from "./AgentTraceFlow.graph";
import type {
  AgentInvocation,
  AgentToolCall,
  DeliverableAcceptance,
  OrchestrationPlanStep,
} from "../../types";

const steps: OrchestrationPlanStep[] = [
  {
    id: "step-positioning",
    agent_code: "01-positioning",
    agent_name: "账号定位专家",
    phase: "定位",
    intent: "校准人设",
    status: "planned",
    depends_on: [],
    expected_output: "定位策略",
    risk_level: "low",
  },
  {
    id: "step-script",
    agent_code: "02-content-director",
    agent_name: "编导文案专家",
    phase: "脚本",
    intent: "生成脚本",
    status: "planned",
    depends_on: ["step-positioning"],
    expected_output: "脚本包",
    risk_level: "medium",
  },
];

const invocations: AgentInvocation[] = [
  {
    id: 1,
    task_id: 10,
    agent_code: "01-positioning",
    agent_name: "账号定位专家",
    status: "done",
    input_summary: "目标",
    output_summary: "定位完成",
    model: "deepseek-chat",
    token_count: 0,
    cost: 0,
    failure_reason: null,
    upstream: [],
    started_at: null,
    finished_at: "2026-07-01T00:00:00Z",
  },
];

const toolCalls: AgentToolCall[] = [
  {
    id: 99,
    org_id: 1,
    task_id: 10,
    invocation_id: 1,
    module: "brain",
    agent_code: "01-positioning",
    tool_code: "account_context",
    tool_name: "Account Context",
    status: "success",
    permission_mode: "auto",
    requires_human_confirmation: false,
    input_summary: "鐩爣",
    output_summary: "璐﹀彿涓婁笅鏂囧凡璇诲彇",
    error: null,
    latency_ms: 10,
    cost: 0,
    meta: {},
    started_at: null,
    finished_at: "2026-07-01T00:00:00Z",
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  },
];

describe("buildAgentTraceGraph", () => {
  it("merges plan steps with invocation state", () => {
    const graph = buildAgentTraceGraph(steps, invocations);

    expect(graph.nodes).toHaveLength(2);
    expect(graph.edges).toEqual([
      expect.objectContaining({
        source: "step-positioning",
        target: "step-script",
      }),
    ]);
    expect(graph.nodes[0].data.status).toBe("done");
    expect(graph.nodes[1].data.status).toBe("planned");
  });

  it("adds quality gates, deliverables, and rerun edges in full mode", () => {
    const acceptance: DeliverableAcceptance = {
      id: 7,
      task_id: 10,
      deliverable_id: 70,
      agent_code: "02-content-director",
      agent_name: "编导文案专家",
      deliverable_type: "video_script",
      title: "脚本包",
      version: 2,
      summary: "第三条脚本需要返工。",
      acceptance_items: [],
      history_versions: [],
      status: "rerun_requested",
      reviewer_note: "开头太像硬广",
      rerun_scope: "current_agent",
      brain_rejudge_summary: "建议仅重跑当前 Agent。",
      brain_rejudge_basis: [],
    };

    const graph = buildAgentTraceGraph({
      steps,
      invocations,
      toolCalls,
      acceptances: [acceptance],
      qualityGates: ["脚本合规"],
      currentFocus: "等待质量门确认：脚本合规",
      mode: "full",
    });

    expect(graph.nodes.map((node) => node.data.kind)).toEqual(
      expect.arrayContaining(["agent", "tool", "gate", "deliverable", "rerun"]),
    );
    expect(graph.nodes.find((node) => node.id === "tool-99")?.data.status).toBe("success");
    expect(graph.nodes.find((node) => node.id === "gate-0")?.data.status).toBe("pending");
    expect(graph.nodes.find((node) => node.id === "deliverable-7")?.data.status).toBe(
      "rerun_requested",
    );
    expect(graph.edges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ source: "deliverable-7", target: "rerun-7" }),
        expect.objectContaining({ source: "rerun-7", target: "step-script" }),
      ]),
    );
  });
});
