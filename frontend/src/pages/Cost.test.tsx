// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Cost from "./Cost";
import { getCostOverview, getTechnicalCostOverview } from "../api/costs";

const businessOverview = {
  scope: {
    client_id: 1,
    client_name: "数码客户",
    project_id: 2,
    project_name: "数码增长",
    period_days: 30,
    period_start: "2026-06-17T00:00:00Z",
    period_end: "2026-07-17T00:00:00Z",
  },
  summary: {
    actual_cost: 12.4,
    budget: 100,
    budget_usage: 12.4,
    remaining_budget: 87.6,
    task_count: 3,
    agent_calls: 8,
    tool_calls: 2,
    failed_operations: 1,
    budget_status: "healthy" as const,
  },
  by_project: [{
    project_id: 2,
    project_name: "数码增长",
    budget: 100,
    actual_cost: 12.4,
    budget_usage: 12.4,
    budget_status: "healthy" as const,
    task_count: 3,
  }],
  by_agent: [{
    agent_code: "02-content-director",
    agent_name: "编导文案专家",
    calls: 5,
    cost: 8.2,
    failed_calls: 0,
  }],
  by_task: [{
    task_id: 12,
    title: "七月冷启动内容",
    type: "content_creation" as const,
    status: "completed",
    agent_calls: 5,
    tool_calls: 2,
    cost: 12.4,
  }],
  by_tool: [{
    tool_code: "publish_package_prepare",
    tool_name: "发布准备",
    calls: 2,
    cost: 4.2,
    failed_calls: 1,
  }],
  daily: [{ date: "2026-07-16", cost: 12.4 }],
};

const technicalOverview = {
  period_days: 30,
  period_start: "2026-06-17T00:00:00Z",
  period_end: "2026-07-17T00:00:00Z",
  summary: {
    total_cost: 0.08,
    total_calls: 2,
    total_tokens: 1000,
    failed_calls: 1,
    fallback_attempts: 1,
    average_latency_ms: 800,
  },
  by_provider: [{
    provider: "deepseek",
    calls: 2,
    tokens: 1000,
    cost: 0.08,
    failed_calls: 1,
    average_latency_ms: 800,
  }],
  by_model: [{
    provider: "deepseek",
    model: "deepseek-reasoner",
    calls: 2,
    tokens: 1000,
    cost: 0.08,
    failed_calls: 1,
    average_latency_ms: 800,
  }],
  by_agent: [{
    agent_code: "00-decision",
    calls: 2,
    tokens: 1000,
    cost: 0.08,
    failed_calls: 1,
  }],
  daily: [{ date: "2026-07-16", calls: 2, failed_calls: 1, cost: 0.08 }],
};

const state = {
  workspace: { clientId: 1 as number | null, projectId: 2 as number | null },
  role: "user" as "user" | "admin",
};

vi.mock("../api/costs", () => ({
  getCostOverview: vi.fn(async () => businessOverview),
  getTechnicalCostOverview: vi.fn(async () => technicalOverview),
}));

vi.mock("../stores/currentWorkspace", () => ({
  useCurrentWorkspace: vi.fn(() => state.workspace),
}));

vi.mock("../stores/auth", () => ({
  useAuth: vi.fn((selector: (value: { user: { role: "user" | "admin" } }) => unknown) =>
    selector({ user: { role: state.role } }),
  ),
}));

vi.mock("echarts-for-react", () => ({ default: () => <div data-testid="cost-chart" /> }));

describe("Cost", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.workspace.clientId = 1;
    state.workspace.projectId = 2;
    state.role = "user";
  });
  afterEach(cleanup);

  function renderPage() {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
      <QueryClientProvider client={client}>
        <AntApp><Cost /></AntApp>
      </QueryClientProvider>,
    );
  }

  it("shows budget and business attribution without technical details", async () => {
    renderPage();

    expect(await screen.findByText("本周期已使用 12.4% 预算")).toBeInTheDocument();
    expect(screen.getByText("七月冷启动内容")).toBeInTheDocument();
    expect(screen.getByText("编导文案专家")).toBeInTheDocument();
    expect(screen.queryByText("技术运行")).not.toBeInTheDocument();
    expect(screen.queryByText("deepseek-reasoner")).not.toBeInTheDocument();
    expect(screen.queryByText("Token")).not.toBeInTheDocument();
    expect(getCostOverview).toHaveBeenCalledWith({ clientId: 1, projectId: 2, days: 30 });
    expect(getTechnicalCostOverview).not.toHaveBeenCalled();
  });

  it("lets admins explicitly open technical telemetry", async () => {
    state.role = "admin";
    renderPage();

    fireEvent.click(await screen.findByRole("radio", { name: "技术运行" }));

    expect(await screen.findByText("deepseek-reasoner")).toBeInTheDocument();
    expect(screen.getAllByText("1,000").length).toBeGreaterThan(0);
    await waitFor(() => expect(getTechnicalCostOverview).toHaveBeenCalledWith(30));
  });

  it("does not load business costs before a client is selected", async () => {
    state.workspace.clientId = null;
    state.workspace.projectId = null;
    renderPage();

    expect(await screen.findByText("先选择一个客户查看成本")).toBeInTheDocument();
    expect(getCostOverview).not.toHaveBeenCalled();
  });
});
