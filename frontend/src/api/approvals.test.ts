import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import { getApprovalWorkspace } from "./approvals";

vi.mock("./client", () => ({
  api: { get: vi.fn() },
}));

const apiGet = api.get as unknown as Mock;

describe("approval workspace api", () => {
  beforeEach(() => vi.resetAllMocks());

  it("loads approvals in the explicit global workspace context", async () => {
    const workspace = { items: [], counts: { total: 0 }, can_decide: false };
    apiGet.mockResolvedValueOnce({ data: workspace });

    await expect(
      getApprovalWorkspace({ client_id: 1, project_id: 2, account_id: null }),
    ).resolves.toEqual(workspace);

    expect(apiGet).toHaveBeenCalledWith("/approvals/workspace", {
      params: { client_id: 1, project_id: 2 },
    });
  });
});
