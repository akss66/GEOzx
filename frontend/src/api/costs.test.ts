import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import { getCostOverview, getTechnicalCostOverview } from "./costs";

vi.mock("./client", () => ({
  api: { get: vi.fn() },
}));

const apiGet = api.get as unknown as Mock;

describe("costs api", () => {
  beforeEach(() => vi.resetAllMocks());

  it("loads business costs in an explicit client and project scope", async () => {
    apiGet.mockResolvedValueOnce({ data: { summary: { actual_cost: 0.1 } } });

    await getCostOverview({ clientId: 3, projectId: 7, days: 30 });

    expect(apiGet).toHaveBeenCalledWith("/costs/overview", {
      params: { client_id: 3, project_id: 7, days: 30 },
    });
  });

  it("loads admin-only technical telemetry separately", async () => {
    apiGet.mockResolvedValueOnce({ data: { summary: { total_cost: 0.08 } } });

    await getTechnicalCostOverview(7);

    expect(apiGet).toHaveBeenCalledWith("/costs/technical", { params: { days: 7 } });
  });
});
