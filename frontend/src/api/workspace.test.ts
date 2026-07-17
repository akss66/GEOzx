import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import { batchUpdateAccounts } from "./workspace";

vi.mock("./client", () => ({
  api: {
    patch: vi.fn(),
  },
}));

const apiPatch = api.patch as unknown as Mock;

describe("workspace api", () => {
  beforeEach(() => vi.resetAllMocks());

  it("updates a selected account set through the atomic batch endpoint", async () => {
    apiPatch.mockResolvedValueOnce({ data: [{ id: 1 }, { id: 2 }] });

    await batchUpdateAccounts({
      account_ids: [1, 2],
      group_id: 7,
      project_id: 9,
      status: "active",
    });

    expect(apiPatch).toHaveBeenCalledWith("/accounts/batch", {
      account_ids: [1, 2],
      group_id: 7,
      project_id: 9,
      status: "active",
    });
  });
});
