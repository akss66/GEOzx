import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import { getCostOverview } from "./costs";
import type { CostOverview } from "../types";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;

describe("costs api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("loads the cost overview endpoint", async () => {
    const overview = {
      total_cost: 0.2,
      total_calls: 2,
      total_tokens: 3000,
      by_brain: [
        {
          type: "review_optimization",
          tasks: 1,
          calls: 2,
          tokens: 3000,
          cost: 0.2,
        },
      ],
      by_model: [{ model: "deepseek-chat", calls: 1, tokens: 2000, cost: 0.12 }],
      by_agent: [
        {
          agent_code: "02-content-director",
          agent_name: "编导文案专家",
          calls: 1,
          tokens: 2000,
          cost: 0.12,
        },
      ],
      by_task: [
        {
          task_id: 12,
          title: "复盘优化任务",
          type: "review_optimization",
          calls: 2,
          tokens: 3000,
          cost: 0.2,
        },
      ],
    } satisfies CostOverview;
    apiGet.mockResolvedValueOnce({ data: overview });

    await expect(getCostOverview()).resolves.toEqual(overview);

    expect(apiGet).toHaveBeenCalledWith("/costs/overview");
  });
});
