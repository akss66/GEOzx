// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ExpertTeam, { describeExpertScope } from "./ExpertTeam";
import { approveDeliverableAcceptance } from "../api/brain";
import { invokeAgent, listAgentRuns, listAgents, suggestAgentRunKnowledge } from "../api/agents";
import { getWorkspaceContext } from "../api/shell";
import type { AgentDirectRun, AgentProfile } from "../types";

const agent: AgentProfile = {
  code: "01-positioning",
  name: "账号定位专家",
  group: "strategy",
  one_liner: "校准账号人设、赛道差异和内容支柱。",
  model: "deepseek-chat",
  fallback_model: null,
  automation_level: "confirm",
  tools: ["账号矩阵", "竞品样本"],
  typical_tasks: ["定位校准", "账号诊断"],
  standard_outputs: ["positioning_strategy"],
  current_task: null,
  tool_summary: { total_calls: 0, pending_approvals: 0, failed_calls: 0, recent_calls: [] },
};

const run = {
  task: {
    id: 18,
    content_item_id: 44,
    title: "直接调用 · 账号定位专家",
    type: "account_diagnosis",
    status: "pending_acceptance",
    brief: {
      goal: "重新判断账号定位",
      project_id: 2,
      project_name: "数码增长项目",
      account_group_id: null,
      account_group_name: null,
      platforms: ["douyin"],
      account_ids: [3],
      cycle: "独立专家调用",
      budget: null,
      content_goal: "产出定位",
      risk_constraints: [],
      expected_outputs: ["账号定位方案"],
      confirmation_actions: ["采用成果"],
    },
    plan: { id: 19, summary: "独立调用", steps: [], quality_gates: [], estimated_cost: 0, requires_human_confirmation: true },
    progress: 90,
    current_focus: "专家成果等待人工采用",
    risk_count: 0,
    runtime_mode: "direct_agent",
    thread_id: "direct-agent-18",
    context_closed_at: null,
    created_at: "2026-07-17T00:00:00Z",
    updated_at: "2026-07-17T00:00:00Z",
  },
  invocation: {
    id: 20,
    task_id: 18,
    agent_code: "01-positioning",
    agent_name: "账号定位专家",
    status: "done",
    input_summary: "重新判断账号定位",
    output_summary: "敢说真话的数码评测账号",
    model: "deepseek-chat",
    token_count: 0,
    cost: 0,
    failure_reason: null,
    upstream: [],
    started_at: "2026-07-17T00:00:00Z",
    finished_at: "2026-07-17T00:01:00Z",
  },
  deliverable: {
    id: 30,
    agent_code: "01-positioning",
    type: "positioning_strategy",
    version: 1,
    status: "pending_review",
    payload: {
      account_persona: "敢说真话的数码评测账号",
      target_audience: "理性数码消费者",
      differentiation: ["拒绝参数堆砌", "优先真实体验"],
      content_pillars: ["产品实测", "选购建议"],
    },
    created_at: "2026-07-17T00:01:00Z",
  },
  acceptance: {
    id: 31,
    task_id: 18,
    deliverable_id: 30,
    agent_code: "01-positioning",
    agent_name: "账号定位专家",
    deliverable_type: "positioning_strategy",
    title: "账号定位方案",
    version: 1,
    summary: "敢说真话的数码评测账号",
    acceptance_items: [],
    history_versions: [],
    status: "pending",
    reviewer_note: null,
    rerun_scope: null,
    brain_rejudge_summary: null,
    brain_rejudge_basis: [],
  },
  knowledge_sources: [],
  message: "专家已完成本轮处理，成果等待你确认是否采用。",
} satisfies AgentDirectRun;

vi.mock("../api/agents", () => ({
  handoffAgentRun: vi.fn(async () => ({ task_id: 18, project_id: 2, account_id: 3, prompt: "继续" })),
  invokeAgent: vi.fn(async () => run),
  listAgentRuns: vi.fn(async () => [run]),
  listAgents: vi.fn(async () => [agent]),
  suggestAgentRunKnowledge: vi.fn(async () => ({ id: 41, status: "pending" })),
}));

vi.mock("../api/brain", () => ({
  approveDeliverableAcceptance: vi.fn(async () => ({ ...run.acceptance, status: "approved" })),
}));

vi.mock("../api/shell", () => ({
  getWorkspaceContext: vi.fn(async () => ({
    clients: [],
    selected_client: null,
    projects: [{ id: 2, name: "数码增长项目" }],
    selected_project: { id: 2, name: "数码增长项目" },
    accounts: [{
      id: 3,
      nickname: "数码菌",
      platform: "douyin",
      status: "active",
      auth_status: "authorized",
    }],
  })),
}));

vi.mock("../stores/currentWorkspace", async () => {
  const actual = await vi.importActual<typeof import("../stores/currentWorkspace")>("../stores/currentWorkspace");
  const state = { clientId: 1, projectId: 2, platform: "douyin", accountId: 3 };
  return {
    ...actual,
    useCurrentWorkspace: vi.fn((selector?: (value: typeof state) => unknown) =>
      selector ? selector(state) : state,
    ),
  };
});

describe("ExpertTeam", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  function renderPage() {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <AntApp><ExpertTeam /></AntApp>
        </QueryClientProvider>
      </MemoryRouter>,
    );
  }

  it("renders an account-scoped expert studio and a readable formal result", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "账号定位专家" })).toBeInTheDocument();
    expect(screen.getByText("数码增长项目 · 数码菌")).toBeInTheDocument();
    expect(await screen.findByText("敢说真话的数码评测账号")).toBeInTheDocument();
    expect(screen.getByText("理性数码消费者")).toBeInTheDocument();
    expect(screen.getByText("拒绝参数堆砌")).toBeInTheDocument();
    expect(screen.queryByText("deepseek-chat")).not.toBeInTheDocument();
    expect(listAgentRuns).toHaveBeenCalledWith("01-positioning", 2, 3);
  });

  it("shows a recoverable state when the expert directory is unavailable", async () => {
    vi.mocked(listAgents).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("专家目录加载失败");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByRole("heading", { name: "账号定位专家" })).toBeInTheDocument();
  });

  it("does not present a failed workspace request as a missing selection", async () => {
    vi.mocked(getWorkspaceContext).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("专家工作区加载失败");
    expect(screen.queryByText("当前账号已选定，请再选择项目。")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("数码增长项目 · 数码菌")).toBeInTheDocument();
  });

  it("does not present failed expert history as no work records", async () => {
    vi.mocked(listAgentRuns).mockRejectedValueOnce({ response: { status: 503 } });

    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("专家记录加载失败");
    expect(screen.queryByText("本账号还没有该专家的工作记录。")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findByText("敢说真话的数码评测账号")).toBeInTheDocument();
  });

  it("starts a new expert run in the selected project and account", async () => {
    renderPage();
    const input = await screen.findByRole("textbox", { name: "专家任务" });
    fireEvent.change(input, { target: { value: "重新分析内容支柱" } });
    fireEvent.click(screen.getByRole("button", { name: "开始分析" }));

    await waitFor(() => expect(invokeAgent).toHaveBeenCalledWith("01-positioning", {
      prompt: "重新分析内容支柱",
      projectId: 2,
      accountId: 3,
      sourceTaskId: undefined,
    }));
  });

  it("requires an explicit confirmation before adopting the result", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "采用成果" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认采用" }));

    await waitFor(() => expect(approveDeliverableAcceptance).toHaveBeenCalledWith(run.acceptance));
  });

  it("sends an expert result to knowledge suggestions without writing directly", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "建议沉淀到知识库" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认送审" }));

    await waitFor(() => expect(suggestAgentRunKnowledge).toHaveBeenCalledWith(run.task.id));
  });

  it("keeps a selected account visible when only the project is missing", () => {
    expect(describeExpertScope(null, "抖音开发测试账号")).toEqual({
      context: "尚未选择项目 · 抖音开发测试账号",
      instruction: "当前账号已选定，请再选择项目。",
    });
  });
});
