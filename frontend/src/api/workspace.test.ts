import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import {
  batchUpdateAccounts,
  createDouyinIncrementalAuthorizeUrl,
  getDouyinAccountCapabilities,
} from "./workspace";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPost = api.post as unknown as Mock;
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

  it("loads the official capability diagnosis for one Douyin account", async () => {
    apiGet.mockResolvedValueOnce({
      data: {
        account_id: 9,
        platform: "douyin",
        configured_app_scopes: ["user_info"],
        granted_account_scopes: ["user_info"],
        capabilities: [],
        next_recommended: "posting_feedback",
      },
    });

    await getDouyinAccountCapabilities(9);

    expect(apiGet).toHaveBeenCalledWith(
      "/platform-integrations/douyin/accounts/9/capabilities",
    );
  });

  it("requests only the missing account authorization for a capability", async () => {
    apiPost.mockResolvedValueOnce({ data: { authorization_url: "https://open.douyin.com/auth" } });

    await createDouyinIncrementalAuthorizeUrl(9, "posting_feedback");

    expect(apiPost).toHaveBeenCalledWith(
      "/platform-integrations/douyin/oauth/incremental-authorize",
      { account_id: 9, capability_key: "posting_feedback" },
    );
  });
});
