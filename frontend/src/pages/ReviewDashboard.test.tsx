// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { sendOptimizationSuggestionToBrain } from "../api/feedback";
import { getReviewWorkspace, upsertReviewGoal } from "../api/metrics";
import ReviewDashboard from "./ReviewDashboard";

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

const mocks = vi.hoisted(() => ({
  accountId: 3 as number | null,
  workspace: {
    account: {
      id: 3,
      nickname: "数码菌",
      platform: "douyin",
      auth_status: "authorized",
      data_sync_status: "healthy",
    },
    period: {
      days: 30,
      current_start: "2026-06-18",
      current_end: "2026-07-17",
      previous_start: "2026-05-19",
      previous_end: "2026-06-17",
    },
    data_status: {
      has_data: true,
      sources: ["douyin"],
      latest_stat_date: "2026-07-16",
      latest_synced_at: "2026-07-17T01:20:00Z",
      latest_confirmed_at: "2026-07-17T01:20:00Z",
      days_since_observed: 1,
      days_since_confirmed: 1,
      coverage: {
        account_metrics: "missing",
        content_metrics: "available",
        content_identity: "available",
        audience: "missing",
        benchmarks: "missing",
      },
      conflict_count: 0,
      source_summary: [],
      missing_reasons: [],
    },
    goal: {
      id: 4,
      period_days: 30,
      target_play: 10000,
      target_completion_rate: 0.5,
      target_follower_delta: 100,
      status: "behind",
      achievement_percent: 68.5,
      components: [
        {
          metric: "play",
          label: "播放量",
          current: 8000,
          target: 10000,
          achievement_percent: 80,
        },
      ],
      summary: "近 30 天目标整体完成 68.5%",
    },
    conclusion: "播放量较上一周期提升 24.0%；目标仍有差距，优先复用高完播开场。",
    totals: {
      play: 8000,
      exposure: 22000,
      avg_completion_rate: 0.42,
      avg_engagement_rate: 0.11,
      follower_delta: 64,
    },
    changes: [
      {
        metric: "play",
        label: "播放量",
        current: 8000,
        previous: 6450,
        delta_percent: 24,
        direction: "up",
        summary: "播放量较上一周期提升24.0%",
      },
      {
        metric: "completion_rate",
        label: "平均完播率",
        current: 42,
        previous: 36,
        delta_percent: 16.7,
        direction: "up",
        summary: "平均完播率较上一周期提升16.7%",
      },
    ],
    trend: [
      { date: "07/15", play: 3000, exposure: 9000 },
      { date: "07/16", play: 5000, exposure: 13000 },
    ],
    engagement: [
      { date: "07/15", completion_rate: 0.4, like_rate: 0.08 },
      { date: "07/16", completion_rate: 0.44, like_rate: 0.09 },
    ],
    attributions: [
      {
        content_item_id: 7,
        title: "手机续航实测",
        play: 5000,
        completion_rate: 0.46,
        engagement_rate: 0.12,
        role: "driver",
        reason: "本周期播放贡献最高，是当前增长驱动内容",
      },
    ],
    evidence: [
      {
        id: 9,
        content_item_id: 7,
        account_id: 3,
        source: "douyin",
        stat_date: "2026-07-16",
        title: "手机续航实测",
        play: 5000,
        exposure: 13000,
        completion_rate: 0.46,
        like_rate: 0.09,
        comment_rate: 0.02,
        share_rate: 0.01,
        follower_delta: 40,
        created_at: "2026-07-17T01:20:00Z",
      },
    ],
    suggestions: [
      {
        id: 12,
        content_item_id: 7,
        content_title: "手机续航实测",
        source_deliverable_id: 5,
        target_stage: "content_direction",
        suggestion: "下一轮保留结论前置，并测试更强的冲突开场",
        status: "suggested",
        note: null,
        accepted_at: null,
        verified_at: null,
        created_at: "2026-07-17T01:20:00Z",
      },
    ],
  },
}));

vi.mock("../api/metrics", () => ({
  getReviewOverview: vi.fn(),
  listPerformanceSnapshots: vi.fn(),
  getReviewWorkspace: vi.fn(async () => mocks.workspace),
  upsertReviewGoal: vi.fn(async () => mocks.workspace.goal),
}));

vi.mock("../api/feedback", () => ({
  listOptimizationSuggestions: vi.fn(async () => []),
  updateOptimizationSuggestion: vi.fn(),
  sendOptimizationSuggestionToBrain: vi.fn(async () => ({ id: 99 })),
}));

vi.mock("../stores/currentWorkspace", () => ({
  useCurrentWorkspace: vi.fn((selector: (state: unknown) => unknown) =>
    selector({ accountId: mocks.accountId }),
  ),
}));

vi.mock("../stores/theme", () => ({
  useThemeMode: vi.fn(() => "light"),
}));

vi.mock("echarts-for-react", () => ({
  default: () => <div data-testid="review-evidence-chart" />,
}));

function renderReview() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <AntApp>
          <ReviewDashboard />
        </AntApp>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("ReviewDashboard", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    mocks.accountId = 3;
  });

  it("requires the explicit top account instead of loading an organization-wide dashboard", () => {
    mocks.accountId = null;
    renderReview();

    expect(screen.getByText("先选择一个抖音账号")).toBeInTheDocument();
    expect(screen.queryByTestId("review-evidence-chart")).not.toBeInTheDocument();
    expect(getReviewWorkspace).not.toHaveBeenCalled();
  });

  it("renders a conclusion-first review narrative with source and next-cycle action", async () => {
    renderReview();

    expect(await screen.findByText(mocks.workspace.conclusion)).toBeInTheDocument();
    expect(screen.getByText("目标完成度")).toBeInTheDocument();
    expect(screen.getByText("关键变化")).toBeInTheDocument();
    expect(screen.getByText("内容归因")).toBeInTheDocument();
    expect(screen.getByText("数据证据")).toBeInTheDocument();
    expect(screen.getByText("Agent 建议")).toBeInTheDocument();
    expect(screen.getByText("抖音回流")).toBeInTheDocument();
    expect(screen.getAllByText("手机续航实测").length).toBeGreaterThan(0);
    expect(screen.getByText("下一轮保留结论前置，并测试更强的冲突开场")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /创建下一轮任务/ })).toBeEnabled();
    expect(screen.getByTestId("review-evidence-chart")).toBeInTheDocument();
  });

  it("shows stale and conflicted data without presenting a fresh conclusion", async () => {
    const originalConclusion = mocks.workspace.conclusion;
    mocks.workspace.data_status.days_since_observed = 12;
    mocks.workspace.data_status.conflict_count = 2;

    renderReview();

    expect(await screen.findByText("数据已过期")).toBeInTheDocument();
    expect(screen.getByText("2 项待处理冲突")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "更新数据" })).toHaveAttribute(
      "href",
      "/accounts/3/data",
    );
    expect(screen.queryByText(originalConclusion)).not.toBeInTheDocument();

    mocks.workspace.data_status.days_since_observed = 1;
    mocks.workspace.data_status.conflict_count = 0;
  });

  it("saves the selected period goal and can send a suggestion to the main agent", async () => {
    renderReview();
    await screen.findByText(mocks.workspace.conclusion);

    fireEvent.click(screen.getByRole("button", { name: /调整目标/ }));
    const playInput = await screen.findByLabelText("目标播放量");
    fireEvent.change(playInput, { target: { value: "12000" } });
    fireEvent.click(screen.getByRole("button", { name: "保存目标" }));

    await waitFor(() =>
      expect(upsertReviewGoal).toHaveBeenCalledWith(
        3,
        expect.objectContaining({ period_days: 30, target_play: 12000 }),
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: /创建下一轮任务/ }));
    await waitFor(() => {
      expect(vi.mocked(sendOptimizationSuggestionToBrain).mock.calls[0]?.[0]).toBe(12);
    });
  });

  it("shows an actionable goal state when the selected period has no target", async () => {
    const originalGoal = { ...mocks.workspace.goal };
    Object.assign(mocks.workspace.goal, {
      id: null,
      target_play: null,
      target_completion_rate: null,
      target_follower_delta: null,
      status: "not_configured",
      achievement_percent: null,
      components: [],
      summary: "尚未设置近 30 天运营目标",
    });

    try {
      renderReview();
      expect(await screen.findByText("为近 30 天设定衡量标准")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /设置周期目标/ })).toBeEnabled();
    } finally {
      Object.assign(mocks.workspace.goal, originalGoal);
    }
  });
});
