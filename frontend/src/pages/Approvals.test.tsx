// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import Approvals from "./Approvals";
import type { AgentToolCall } from "../types";

const toolCall: AgentToolCall = {
  id: 45,
  org_id: 1,
  task_id: 12,
  invocation_id: 90,
  module: "brain",
  agent_code: "02-content-director",
  tool_code: "brief_builder",
  tool_name: "Brief Builder",
  status: "waiting_approval",
  permission_mode: "confirm",
  requires_human_confirmation: true,
  input_summary: "账号目标与内容方向",
  output_summary: "Brief 已生成，等待人工确认",
  error: null,
  latency_ms: 20,
  cost: 0,
  meta: { agent_name: "编导文案专家" },
  started_at: null,
  finished_at: null,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
};

const publishToolCall: AgentToolCall = {
  ...toolCall,
  id: 46,
  task_id: 13,
  invocation_id: null,
  module: "content_production",
  agent_code: "06-operator",
  tool_code: "publish_package_prepare",
  tool_name: "Publish Package Prepare",
  input_summary: "douyin publish check",
  output_summary: "Ready for manual confirmation",
  meta: {
    content_item_id: 88,
    matrix_plan_id: 21,
    matrix_item_id: 22,
    content_title: "Launch Content",
    platform: "douyin",
    publish_title: "launch title",
    body: "launch body",
    topics: ["agent", "ops"],
    scheduled_at: null,
    material_ids: [7, 8],
    cover_material_id: 8,
    visibility: "friends",
    allow_comment: false,
    publish_package: {
      platform: "douyin",
      account_id: 6,
      content_type: "video",
      title: "launch title",
      body: "launch body",
      topics: ["agent", "ops"],
      scheduled_at: null,
      material_ids: [7, 8],
      cover_material_id: 8,
      visibility: "friends",
      allow_comment: false,
      execution_mode: "manual_checklist",
      manual_steps: ["打开抖音创作者服务中心", "上传素材 #7 / #8"],
    },
    risk: "pass",
    findings: [
      {
        level: "pass",
        code: "material.ok",
        message: "Material #7 is publishable.",
      },
    ],
  },
};

vi.mock("../api/orchestrator", () => ({
  approveGate: vi.fn(),
  listPendingGates: vi.fn(async () => []),
}));

vi.mock("../api/brain", () => ({
  approveToolCall: vi.fn(async () => ({ ...toolCall, status: "success" })),
  listPendingToolCallApprovals: vi.fn(async () => [toolCall, publishToolCall]),
}));

vi.mock("../hooks/useEventStream", () => ({
  useEventStream: vi.fn(),
}));

describe("Approvals", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders pending Agent tool call approvals", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <AntApp>
          <Approvals />
        </AntApp>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("Agent 工具确认")).toBeInTheDocument();
    expect(screen.getByText("Brief Builder")).toBeInTheDocument();
    expect(screen.getByText("Launch Content")).toBeInTheDocument();
    expect(screen.getByText("矩阵计划 #21")).toBeInTheDocument();
    expect(screen.getByText("子任务 #22")).toBeInTheDocument();
    expect(screen.getByText("launch title")).toBeInTheDocument();
    expect(screen.getByText("视频")).toBeInTheDocument();
    expect(screen.getByText("账号 #6")).toBeInTheDocument();
    expect(screen.getByText("#7 / #8")).toBeInTheDocument();
    expect(screen.getByText("朋友可见")).toBeInTheDocument();
    expect(screen.getByText("关闭评论")).toBeInTheDocument();
    expect(screen.getByText("#8")).toBeInTheDocument();
    expect(screen.getByText("人工发布清单")).toBeInTheDocument();
    expect(screen.getByText("打开抖音创作者服务中心")).toBeInTheDocument();
    expect(screen.getByText("material.ok")).toBeInTheDocument();
    expect(screen.getByText("编导文案专家")).toBeInTheDocument();
    expect(screen.getByText("Brief 已生成，等待人工确认")).toBeInTheDocument();
  });
});
