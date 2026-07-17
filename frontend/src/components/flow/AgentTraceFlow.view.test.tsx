// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AgentToolCall } from "../../types";
import type { TraceNodeData } from "./AgentTraceFlow.graph";
import { TraceDetails } from "./AgentTraceFlow";

describe("TraceDetails", () => {
  it("renders tool details with readable Chinese labels", () => {
    const toolCall: AgentToolCall = {
      id: 9,
      org_id: 1,
      task_id: 10,
      invocation_id: 2,
      module: "brain",
      agent_code: "01-positioning",
      tool_code: "account_context",
      tool_name: "账号上下文",
      status: "failed",
      permission_mode: "confirm",
      requires_human_confirmation: true,
      input_summary: "读取当前账号",
      output_summary: "",
      error: "账号授权已失效",
      latency_ms: 12,
      cost: 0,
      meta: {},
      started_at: null,
      finished_at: null,
      created_at: "2026-07-17T00:00:00Z",
      updated_at: "2026-07-17T00:00:00Z",
    };
    const data: TraceNodeData = {
      label: "账号上下文",
      status: "failed",
      kind: "tool",
      meta: "",
      toolCall,
    };

    render(<TraceDetails data={data} />);

    for (const label of ["工具", "权限", "输入", "输出", "审批", "失败"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("暂无输出摘要")).toBeInTheDocument();
    expect(screen.getByText("需要人工确认后继续")).toBeInTheDocument();
  });
});
