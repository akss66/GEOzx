import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import { listRiskQueue } from "./risks";
import type { RiskQueueItem } from "../types";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;

describe("risks api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("loads the risk queue endpoint", async () => {
    const risks = [
      {
        id: "gate:1",
        category: "quality_gate",
        severity: "high",
        title: "脚本合规待审批",
        description: "内容等待人工质量门处理",
        source: "内容 #1",
        status: "pending",
        created_at: "2026-07-01T00:00:00Z",
      },
    ] satisfies RiskQueueItem[];
    apiGet.mockResolvedValueOnce({ data: risks });

    await expect(listRiskQueue()).resolves.toEqual(risks);

    expect(apiGet).toHaveBeenCalledWith("/risks/queue");
  });
});
