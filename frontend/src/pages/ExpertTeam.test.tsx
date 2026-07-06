// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import ExpertTeam from "./ExpertTeam";
import type { AgentProfile } from "../types";

const agent: AgentProfile = {
  code: "02-content-director",
  name: "编导文案专家",
  group: "creative",
  one_liner: "把定位转成脚本。",
  model: "deepseek-chat",
  fallback_model: null,
  automation_level: "confirm",
  tools: ["脚本库"],
  typical_tasks: ["脚本包"],
  standard_outputs: ["video_script"],
  current_task: null,
  tool_summary: {
    total_calls: 1,
    pending_approvals: 1,
    failed_calls: 0,
    recent_calls: [
      {
        id: 45,
        task_id: 12,
        tool_code: "brief_builder",
        tool_name: "Brief Builder",
        status: "waiting_approval",
        permission_mode: "confirm",
        requires_human_confirmation: true,
        input_summary: "账号定位和内容目标",
        output_summary: "Brief 已生成，等待人工确认",
        error: null,
        created_at: "2026-07-01T00:00:00Z",
      },
    ],
  },
};

vi.mock("../api/agents", () => ({
  invokeAgent: vi.fn(),
  listAgents: vi.fn(async () => [agent]),
}));

vi.mock("../stores/auth", () => ({
  useAuth: vi.fn((selector: (state: unknown) => unknown) =>
    selector({ user: { role: "admin" } }),
  ),
}));

describe("ExpertTeam", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders real tool call summaries for the selected expert", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <AntApp>
            <ExpertTeam />
          </AntApp>
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("工具账本")).toBeInTheDocument();
    expect(screen.getByText("有工具调用等待人工审批")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "去审批" })).toBeInTheDocument();
    expect(screen.getByText("Brief Builder")).toBeInTheDocument();
    expect(screen.getByText("待人工审批")).toBeInTheDocument();
    expect(screen.getByText("需确认")).toBeInTheDocument();
    expect(screen.getByText("人工门")).toBeInTheDocument();
    expect(screen.getByText("Brief 已生成，等待人工确认")).toBeInTheDocument();
  });
});
