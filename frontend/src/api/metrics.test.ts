import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  getReviewOverview,
  getReviewWorkspace,
  listPerformanceSnapshots,
  upsertReviewGoal,
} from "./metrics";
import { api } from "./client";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    put: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPut = api.put as unknown as Mock;

describe("metrics api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("loads review overview and performance snapshots", async () => {
    const overview = {
      has_data: false,
      trend: [],
      engagement: [],
      rank_top: [],
      rank_bottom: [],
      total_play: 0,
      avg_completion_rate: 0,
      follower_delta: 0,
    };
    const snapshots = [
      {
        id: 1,
        content_item_id: 8,
        account_id: 3,
        source: "douyin",
        stat_date: "2026-07-06",
        title: "Matrix launch",
        play: 12000,
        exposure: 30000,
        completion_rate: 0.42,
        like_rate: 0.08,
        comment_rate: 0.02,
        share_rate: 0.01,
        follower_delta: 0,
        created_at: "2026-07-06T00:00:00Z",
      },
    ];

    apiGet.mockResolvedValueOnce({ data: overview });
    apiGet.mockResolvedValueOnce({ data: snapshots });

    await expect(getReviewOverview(30)).resolves.toEqual(overview);
    await expect(listPerformanceSnapshots(3)).resolves.toEqual(snapshots);

    expect(apiGet).toHaveBeenNthCalledWith(1, "/metrics/overview", {
      params: { days: 30 },
    });
    expect(apiGet).toHaveBeenNthCalledWith(2, "/metrics/performance-snapshots", {
      params: { account_id: 3 },
    });
  });

  it("loads an account-scoped review narrative and saves its period goal", async () => {
    const workspace = {
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
        has_data: false,
        sources: [],
        latest_stat_date: null,
        latest_synced_at: null,
        missing_reasons: ["该账号近 30 天没有真实指标快照"],
      },
      goal: {
        id: null,
        period_days: 30,
        status: "not_configured",
        achievement_percent: null,
        components: [],
        summary: "尚未设置近 30 天运营目标",
      },
      conclusion: "尚未形成可复盘的数据周期，先完成账号数据同步。",
      totals: {
        play: 0,
        exposure: 0,
        avg_completion_rate: 0,
        avg_engagement_rate: 0,
        follower_delta: 0,
      },
      changes: [],
      trend: [],
      engagement: [],
      attributions: [],
      evidence: [],
      suggestions: [],
    };
    const goal = {
      id: 9,
      period_days: 30,
      target_play: 10000,
      target_completion_rate: 0.4,
      target_follower_delta: 100,
      status: "insufficient_data",
      achievement_percent: null,
      components: [],
      summary: "周期目标已保存",
    };
    apiGet.mockResolvedValueOnce({ data: workspace });
    apiPut.mockResolvedValueOnce({ data: goal });

    await expect(getReviewWorkspace(3, 30)).resolves.toEqual(workspace);
    await expect(
      upsertReviewGoal(3, {
        period_days: 30,
        target_play: 10000,
        target_completion_rate: 0.4,
        target_follower_delta: 100,
      }),
    ).resolves.toEqual(goal);

    expect(apiGet).toHaveBeenCalledWith("/metrics/review-workspace", {
      params: { account_id: 3, days: 30 },
    });
    expect(apiPut).toHaveBeenCalledWith("/metrics/review-goals/3", {
      period_days: 30,
      target_play: 10000,
      target_completion_rate: 0.4,
      target_follower_delta: 100,
    });
  });
});
