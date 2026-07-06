import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import {
  createDouyinAuthorizeUrl,
  createDouyinJsSignature,
  createDouyinScanAddUrl,
  createDouyinTrialWhitelistUrl,
  createDistributionAction,
  getAccountMatrix,
  listPlatformIntegrations,
  syncDouyinAccountMetrics,
  updateAccountIntegration,
  updatePlatformIntegration,
} from "./workspace";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPatch = api.patch as unknown as Mock;
const apiPost = api.post as unknown as Mock;

describe("workspace api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("loads the account matrix endpoint", async () => {
    const matrix = {
      groups: [],
      ungrouped_accounts: [],
      platforms: [],
    };
    apiGet.mockResolvedValueOnce({ data: matrix });

    await expect(getAccountMatrix()).resolves.toEqual(matrix);

    expect(apiGet).toHaveBeenCalledWith("/account-matrix", { params: undefined });

    apiGet.mockResolvedValueOnce({ data: matrix });
    await expect(getAccountMatrix(3)).resolves.toEqual(matrix);
    expect(apiGet).toHaveBeenCalledWith("/account-matrix", { params: { project_id: 3 } });
  });

  it("updates account integration status", async () => {
    const account = {
      id: 1,
      nickname: "抖音授权号",
      platform: "douyin",
      group_id: null,
      project_id: null,
      status: "active",
      external_account_id: null,
      integration_status: "connected",
      auth_status: "authorized",
      data_sync_status: "healthy",
      created_at: "2026-07-01T00:00:00Z",
    };
    apiPatch.mockResolvedValueOnce({ data: account });

    await expect(
      updateAccountIntegration(1, {
        integration_status: "connected",
        auth_status: "authorized",
        data_sync_status: "healthy",
        note: "OAuth 已完成",
      }),
    ).resolves.toEqual(account);

    expect(apiPatch).toHaveBeenCalledWith("/accounts/1/integration", {
      integration_status: "connected",
      auth_status: "authorized",
      data_sync_status: "healthy",
      note: "OAuth 已完成",
    });
  });

  it("loads and updates platform integrations", async () => {
    const integrations = [
      {
        id: 1,
        platform: "douyin",
        status: "configured",
        client_key: "client-key",
        client_secret_configured: true,
        redirect_uri: "https://example.com/callback",
        js_sdk_domain: "https://example.com",
        auth_status: "not_configured",
        data_sync_status: "not_configured",
        scopes: ["user_info"],
        capabilities: {},
        official_docs: [],
        note: null,
        created_at: "2026-07-02T00:00:00Z",
        updated_at: "2026-07-02T00:00:00Z",
      },
    ];
    apiGet.mockResolvedValueOnce({ data: integrations });

    await expect(listPlatformIntegrations()).resolves.toEqual(integrations);
    expect(apiGet).toHaveBeenCalledWith("/platform-integrations");

    apiPatch.mockResolvedValueOnce({ data: integrations[0] });
    await expect(
      updatePlatformIntegration("douyin", {
        status: "configured",
        client_key: "client-key",
        client_secret_ref: "env:DOUYIN_CLIENT_SECRET",
      }),
    ).resolves.toEqual(integrations[0]);

    expect(apiPatch).toHaveBeenCalledWith("/platform-integrations/douyin", {
      status: "configured",
      client_key: "client-key",
      client_secret_ref: "env:DOUYIN_CLIENT_SECRET",
    });
  });

  it("creates douyin oauth authorize urls and js signatures", async () => {
    const authorize = {
      platform: "douyin",
      client_key: "client-key",
      redirect_uri: "https://example.com/callback",
      scopes: ["user_info"],
      state: "signed-state",
      authorization_url: "https://open.douyin.com/platform/oauth/connect/?state=signed-state",
    };
    apiPost.mockResolvedValueOnce({ data: authorize });

    await expect(createDouyinAuthorizeUrl(7)).resolves.toEqual(authorize);
    expect(apiPost).toHaveBeenCalledWith(
      "/platform-integrations/douyin/oauth/authorize",
      { account_id: 7 },
    );

    apiPost.mockResolvedValueOnce({ data: authorize });

    await expect(
      createDouyinScanAddUrl({ nickname: "扫码号", group_id: null, project_id: null }),
    ).resolves.toEqual(authorize);
    expect(apiPost).toHaveBeenCalledWith(
      "/platform-integrations/douyin/oauth/scan-add",
      { nickname: "扫码号", group_id: null, project_id: null },
    );

    const whitelist = {
      platform: "douyin",
      client_key: "client-key",
      redirect_uri: "https://example.com/callback",
      scopes: ["trial.whitelist"],
      authorization_url:
        "https://open.douyin.com/platform/oauth/connect?scope=trial.whitelist",
    };
    apiPost.mockResolvedValueOnce({ data: whitelist });

    await expect(createDouyinTrialWhitelistUrl()).resolves.toEqual(whitelist);
    expect(apiPost).toHaveBeenCalledWith(
      "/platform-integrations/douyin/oauth/trial-whitelist",
    );

    const signature = {
      platform: "douyin",
      client_key: "client-key",
      nonce_str: "nonce",
      timestamp: 123,
      url: "https://example.com/page",
      signature: "signature",
    };
    apiPost.mockResolvedValueOnce({ data: signature });

    await expect(createDouyinJsSignature("https://example.com/page")).resolves.toEqual(signature);
    expect(apiPost).toHaveBeenCalledWith(
      "/platform-integrations/douyin/js-signature",
      { url: "https://example.com/page" },
    );
  });

  it("syncs douyin account metrics", async () => {
    const result = {
      account_id: 7,
      platform: "douyin",
      data_sync_status: "healthy",
      profile_synced: true,
      video_count: 2,
      snapshot_count: 2,
      last_sync_at: "2026-07-06T06:30:00Z",
    };
    apiPost.mockResolvedValueOnce({ data: result });

    await expect(syncDouyinAccountMetrics(7)).resolves.toEqual(result);

    expect(apiPost).toHaveBeenCalledWith(
      "/platform-integrations/douyin/accounts/7/sync-metrics",
    );
  });

  it("records a distribution action", async () => {
    const action = {
      id: 9,
      platform: "douyin",
      account_ids: [1],
      action_type: "manual_publish",
      status: "recorded",
      content_item_id: null,
      project_id: null,
      note: "已排期",
      created_at: "2026-07-01T00:00:00Z",
    };
    apiPost.mockResolvedValueOnce({ data: action });

    await expect(
      createDistributionAction({
        platform: "douyin",
        account_ids: [1],
        action_type: "manual_publish",
        note: "已排期",
      }),
    ).resolves.toEqual(action);

    expect(apiPost).toHaveBeenCalledWith("/distribution/actions", {
      platform: "douyin",
      account_ids: [1],
      action_type: "manual_publish",
      note: "已排期",
    });
  });
});
