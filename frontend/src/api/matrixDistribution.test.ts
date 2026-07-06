import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  createMatrixDistributionPlan,
  listMatrixDistributionPlans,
} from "./matrixDistribution";
import { api } from "./client";
import type { MatrixDistributionPlan } from "../types";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPost = api.post as unknown as Mock;

const plan = {
  id: 7,
  org_id: 1,
  content_item_id: 12,
  created_by_id: 1,
  title: "Matrix launch",
  body: "body",
  platforms: ["douyin"],
  account_ids: [3, 4],
  material_ids: [9],
  topics: ["agent"],
  cover_material_id: null,
  scheduled_at: null,
  status: "pending_approval",
  items: [],
  created_at: "2026-07-06T00:00:00Z",
  updated_at: "2026-07-06T00:00:00Z",
} satisfies MatrixDistributionPlan;

describe("matrix distribution api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("lists and creates matrix distribution plans", async () => {
    apiGet.mockResolvedValueOnce({ data: [plan] });
    apiPost.mockResolvedValueOnce({ data: plan });

    await expect(listMatrixDistributionPlans()).resolves.toEqual([plan]);
    await expect(
      createMatrixDistributionPlan({
        platforms: ["douyin"],
        account_ids: [3, 4],
        material_ids: [9],
        content_item_id: 12,
        title: "Matrix launch",
        body: "body",
        topics: ["agent"],
      }),
    ).resolves.toEqual(plan);

    expect(apiGet).toHaveBeenCalledWith("/matrix-distribution-plans");
    expect(apiPost).toHaveBeenCalledWith(
      "/matrix-distribution-plans",
      expect.objectContaining({
        account_ids: [3, 4],
        material_ids: [9],
        platforms: ["douyin"],
      }),
    );
  });
});
