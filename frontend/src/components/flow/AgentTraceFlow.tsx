import "@xyflow/react/dist/style.css";

import {
  Background,
  Controls,
  type Edge,
  ReactFlow,
  type NodeMouseHandler,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import { Empty, Tag } from "antd";
import { useEffect, useMemo, useState } from "react";

import type {
  AgentInvocation,
  AgentToolCall,
  DeliverableAcceptance,
  OrchestrationPlanStep,
} from "../../types";
import {
  buildAgentTraceGraph,
  type TraceMode,
  type TraceNode,
  type TraceNodeData,
} from "./AgentTraceFlow.graph";

const STATUS_TONE: Record<string, string> = {
  queued: "var(--dy-faint)",
  planned: "var(--dy-faint)",
  running: "var(--dy-info)",
  done: "var(--dy-success)",
  blocked: "var(--dy-warning)",
  failed: "var(--dy-error)",
  skipped: "var(--dy-muted)",
  success: "var(--dy-success)",
  waiting_approval: "var(--dy-warning)",
  pending: "var(--dy-warning)",
  configured: "var(--dy-faint)",
  approved: "var(--dy-success)",
  rejected: "var(--dy-error)",
  rerun_requested: "var(--dy-warning)",
  current_agent: "var(--dy-warning)",
  upstream: "var(--dy-warning)",
  downstream: "var(--dy-warning)",
  full_chain: "var(--dy-error)",
};

export function AgentTraceFlow({
  steps,
  invocations,
  toolCalls = [],
  acceptances = [],
  qualityGates = [],
  currentFocus = "",
  mode = "simple",
}: {
  steps: OrchestrationPlanStep[];
  invocations: AgentInvocation[];
  toolCalls?: AgentToolCall[];
  acceptances?: DeliverableAcceptance[];
  qualityGates?: string[];
  currentFocus?: string;
  mode?: TraceMode;
}) {
  const graph = useMemo(
    () =>
      buildAgentTraceGraph({
        steps,
        invocations,
        toolCalls,
        acceptances,
        qualityGates,
        currentFocus,
        mode,
      }),
    [acceptances, currentFocus, invocations, mode, qualityGates, steps, toolCalls],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<TraceNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selected, setSelected] = useState<TraceNodeData | null>(null);

  useEffect(() => {
    setNodes(graph.nodes);
    setEdges(graph.edges);
    setSelected(graph.nodes[0]?.data ?? null);
  }, [graph.edges, graph.nodes, setEdges, setNodes]);

  const onNodeClick: NodeMouseHandler<TraceNode> = (_event, node) => {
    setSelected(node.data);
  };

  if (nodes.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无调用链" />;
  }

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div
        style={{
          height: 260,
          border: "1px solid var(--dy-border-subtle)",
          borderRadius: 8,
          overflow: "hidden",
          background: "var(--dy-surface)",
        }}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          fitView
          minZoom={0.45}
          maxZoom={1.4}
          panOnDrag={false}
          nodesDraggable
          nodesConnectable={false}
          selectNodesOnDrag
          onNodeClick={onNodeClick}
        >
          <Background color="var(--dy-border-subtle)" gap={18} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      {selected && <TraceDetails data={selected} />}
    </div>
  );
}

function TraceDetails({ data }: { data: TraceNodeData }) {
  const invocation = data.invocation;
  const toolCall = data.toolCall;
  const acceptance = data.acceptance;
  return (
    <div
      style={{
        border: "1px solid var(--dy-border-subtle)",
        borderRadius: 8,
        padding: 10,
        background: "var(--dy-elevated)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <strong style={{ color: "var(--dy-text)", fontSize: 13 }}>{data.label}</strong>
        <Tag
          style={{
            marginInlineEnd: 0,
            color: STATUS_TONE[data.status] ?? "var(--dy-muted)",
            background: "transparent",
            borderColor: "var(--dy-border)",
          }}
        >
          {data.status}
        </Tag>
      </div>
      {data.kind === "agent" && invocation ? (
        <div style={{ display: "grid", gap: 6, fontSize: 12, color: "var(--dy-muted)" }}>
          <TraceLine label="输入" value={invocation.input_summary} />
          <TraceLine label="输出" value={invocation.output_summary || "暂无输出摘要"} />
          <TraceLine
            label="模型"
            value={`${invocation.model} · token ${invocation.token_count} · $${Number(
              invocation.cost,
            ).toFixed(3)}`}
          />
          {invocation.failure_reason && (
            <TraceLine label="失败" value={invocation.failure_reason} />
          )}
        </div>
      ) : data.kind === "tool" && toolCall ? (
        <div style={{ display: "grid", gap: 6, fontSize: 12 }}>
          <TraceLine label="宸ュ叿" value={toolCall.tool_code} />
          <TraceLine label="鏉冮檺" value={toolCall.permission_mode} />
          <TraceLine label="杈撳叆" value={toolCall.input_summary || "鏆傛棤杈撳叆鎽樿"} />
          <TraceLine label="杈撳嚭" value={toolCall.output_summary || "鏆傛棤杈撳嚭鎽樿"} />
          {toolCall.requires_human_confirmation && (
            <TraceLine label="瀹℃壒" value="闇€瑕佷汉宸ョ‘璁ゅ悗缁х画" />
          )}
          {toolCall.error && <TraceLine label="澶辫触" value={toolCall.error} />}
        </div>
      ) : data.kind === "deliverable" && acceptance ? (
        <div style={{ display: "grid", gap: 6, fontSize: 12 }}>
          <TraceLine label="摘要" value={acceptance.summary || "暂无摘要"} />
          <TraceLine label="版本" value={`v${acceptance.version} · ${acceptance.deliverable_type}`} />
          <TraceLine label="验收" value={acceptance.status} />
          {acceptance.reviewer_note && (
            <TraceLine label="意见" value={acceptance.reviewer_note} />
          )}
        </div>
      ) : data.kind === "rerun" && acceptance ? (
        <div style={{ display: "grid", gap: 6, fontSize: 12 }}>
          <TraceLine label="范围" value={acceptance.rerun_scope ?? "current_agent"} />
          <TraceLine label="原因" value={acceptance.reviewer_note ?? "等待填写返工原因"} />
          {acceptance.brain_rejudge_summary && (
            <TraceLine label="重判" value={acceptance.brain_rejudge_summary} />
          )}
        </div>
      ) : (
        <div style={{ display: "grid", gap: 6, fontSize: 12 }}>
          <TraceLine label={data.kind === "gate" ? "门禁" : "说明"} value={data.meta} />
        </div>
      )}
    </div>
  );
}

function TraceLine({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "38px minmax(0, 1fr)", gap: 8 }}>
      <span style={{ color: "var(--dy-faint)" }}>{label}</span>
      <span style={{ color: "var(--dy-text)", lineHeight: 1.45 }}>{value}</span>
    </div>
  );
}
