// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { App as AntApp } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import BrainHome from "./BrainHome";
import type { Account, BrainTask } from "../types";

const mocks = vi.hoisted(() => {
  const account = {
    id: 3,
    nickname: "A account",
    platform: "douyin",
    group_id: null,
    project_id: null,
    status: "active",
    external_account_id: "a",
    integration_status: "connected",
    auth_status: "authorized",
    data_sync_status: "pending",
    created_at: "2026-07-01T00:00:00Z",
  } satisfies Account;

  const taskWithDecimalString = {
    id: 12,
    content_item_id: null,
    title: "Real API task",
    type: "content_creation",
    status: "pending_acceptance",
    brief: {
      goal: "Goal",
      project_id: null,
      project_name: null,
      account_group_id: null,
      account_group_name: null,
      platforms: ["douyin"],
      account_ids: [3],
      cycle: "This week",
      budget: null,
      content_goal: "Content goal",
      risk_constraints: ["Pre-publish check"],
      expected_outputs: [],
      confirmation_actions: [],
    },
    plan: {
      id: 1,
      summary: "Plan",
      steps: [],
      quality_gates: [],
      estimated_cost: "0.68",
      requires_human_confirmation: true,
    },
    progress: 38,
    current_focus: "Waiting acceptance",
    risk_count: 1,
    context_closed_at: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  } as unknown as BrainTask;

  const matrixTask = {
    id: 13,
    content_item_id: 88,
    title: "矩阵分发：新品视频",
    type: "matrix_distribution",
    status: "pending_acceptance",
    brief: {
      goal: "把 3 条素材发到 A/B 账号",
      project_id: null,
      project_name: null,
      account_group_id: null,
      account_group_name: null,
      platforms: ["douyin"],
      account_ids: [3, 4],
      cycle: "今晚",
      budget: null,
      content_goal: "矩阵发布包准备",
      risk_constraints: [],
      expected_outputs: [],
      confirmation_actions: [],
    },
    plan: {
      id: 2,
      summary: "矩阵分发计划",
      steps: [
        {
          id: "step-operation",
          agent_code: "06-operator",
          agent_name: "发布准备专家",
          phase: "发布准备",
          intent: "生成矩阵发布包",
          status: "planned",
          depends_on: [],
          expected_output: "发布包",
          risk_level: "medium",
          execution_kind: "publish_readiness",
          human_gate: true,
          tool_codes: ["publish_package_prepare"],
        },
      ],
      quality_gates: [],
      estimated_cost: 0.2,
      requires_human_confirmation: true,
    },
    progress: 80,
    current_focus: "矩阵发布包等待人工审批",
    risk_count: 0,
    context_closed_at: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  } as unknown as BrainTask;

  return { account, matrixTask, taskWithDecimalString };
});

vi.mock("../api/workspace", () => ({
  listAccountGroups: vi.fn(async () => []),
  listAccounts: vi.fn(async () => [mocks.account]),
  listProjects: vi.fn(async () => []),
}));

vi.mock("../api/brain", () => ({
  confirmBrainTask: vi.fn(),
  draftBrainTask: vi.fn(),
  listBrainTasks: vi.fn(async () => [mocks.taskWithDecimalString, mocks.matrixTask]),
}));

vi.mock("../stores/currentWorkspace", () => ({
  useCurrentWorkspace: vi.fn(() => ({
    accountId: 3,
    setAccountId: vi.fn(),
  })),
}));

describe("BrainHome", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders decimal costs and matrix distribution breakdowns", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    render(
      <QueryClientProvider client={client}>
        <AntApp>
          <BrainHome />
        </AntApp>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("矩阵分发：新品视频")).toBeInTheDocument();
    expect(screen.getByText("矩阵计划")).toBeInTheDocument();
    expect(screen.getByText("抖音")).toBeInTheDocument();
    expect(screen.getByText("账号 3 / 4")).toBeInTheDocument();
    expect(screen.getByText("发布包准备")).toBeInTheDocument();
  });
});
