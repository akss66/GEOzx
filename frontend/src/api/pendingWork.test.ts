import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import {
  completePendingShootTask,
  getAccountPendingWork,
  pendingWorkQueryKey,
  publishPendingScheduleEntry,
} from "./pendingWork";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPost = api.post as unknown as Mock;

describe("pending work api", () => {
  beforeEach(() => vi.resetAllMocks());

  it("uses one account-specific query key and endpoint", async () => {
    apiGet.mockResolvedValueOnce({ data: { account_id: 9, groups: [] } });

    await getAccountPendingWork(9);

    expect(pendingWorkQueryKey(9)).toEqual(["account-pending-work", 9]);
    expect(apiGet).toHaveBeenCalledWith("/accounts/9/pending-work");
  });

  it("completes shoot and publishing work through scoped lifecycle endpoints", async () => {
    apiPost.mockResolvedValue({ data: { completed: true } });

    await completePendingShootTask(9, 21);
    await publishPendingScheduleEntry(9, 22);

    expect(apiPost).toHaveBeenNthCalledWith(
      1,
      "/accounts/9/pending-work/shoot-tasks/21/complete",
    );
    expect(apiPost).toHaveBeenNthCalledWith(
      2,
      "/accounts/9/pending-work/schedule-entries/22/publish",
    );
  });
});
