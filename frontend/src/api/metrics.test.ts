import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { getReviewOverview, listPerformanceSnapshots } from "./metrics";
import { api } from "./client";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;

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
});
