import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installLocalStorage } from "../test/storage";

describe("useCurrentWorkspace", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults to douyin without a selected account", async () => {
    installLocalStorage();

    const { useCurrentWorkspace } = await import("./currentWorkspace");

    expect(useCurrentWorkspace.getState().platform).toBe("douyin");
    expect(useCurrentWorkspace.getState().clientId).toBeNull();
    expect(useCurrentWorkspace.getState().projectId).toBeNull();
    expect(useCurrentWorkspace.getState().accountId).toBeNull();
  });

  it("loads the persisted account context", async () => {
    installLocalStorage({
      tongzhouxing_current_workspace: JSON.stringify({ platform: "douyin", accountId: 7 }),
    });

    const { useCurrentWorkspace } = await import("./currentWorkspace");

    expect(useCurrentWorkspace.getState().platform).toBe("douyin");
    expect(useCurrentWorkspace.getState().accountId).toBe(7);
  });

  it("restores a persisted wechat official account workspace on reload", async () => {
    installLocalStorage({
      tongzhouxing_current_workspace: JSON.stringify({
        version: 2,
        clientId: 5,
        projectId: 9,
        platform: "wechat_official_account",
        accountId: 11,
      }),
    });

    const { useCurrentWorkspace } = await import("./currentWorkspace");

    expect(useCurrentWorkspace.getState()).toMatchObject({
      clientId: 5,
      projectId: 9,
      platform: "wechat_official_account",
      accountId: 11,
    });
  });

  it("fails closed to douyin when persisted platform is unknown", async () => {
    installLocalStorage({
      tongzhouxing_current_workspace: JSON.stringify({
        version: 2,
        clientId: 5,
        projectId: 9,
        platform: "unknown_platform",
        accountId: 11,
      }),
    });

    const { useCurrentWorkspace } = await import("./currentWorkspace");

    expect(useCurrentWorkspace.getState()).toMatchObject({
      clientId: 5,
      projectId: 9,
      platform: "douyin",
      accountId: 11,
    });
  });

  it("persists account changes", async () => {
    const storage = installLocalStorage();
    const { useCurrentWorkspace } = await import("./currentWorkspace");

    useCurrentWorkspace.getState().setAccountId(9);

    expect(useCurrentWorkspace.getState().accountId).toBe(9);
    expect(storage.setItem).toHaveBeenCalledWith(
      "tongzhouxing_current_workspace",
      JSON.stringify({
        version: 2,
        clientId: null,
        projectId: null,
        platform: "douyin",
        accountId: 9,
      }),
    );
  });

  it("clears the project but preserves the independent work account when the client changes", async () => {
    installLocalStorage();
    const { useCurrentWorkspace } = await import("./currentWorkspace");

    useCurrentWorkspace.getState().hydrate({
      clientId: 1,
      projectId: 2,
      platform: "douyin",
      accountId: 3,
    });
    useCurrentWorkspace.getState().setClientId(9);

    expect(useCurrentWorkspace.getState()).toMatchObject({
      clientId: 9,
      projectId: null,
      platform: "douyin",
      accountId: 3,
    });
  });

  it("keeps the current client and project when opening an unbound account", async () => {
    installLocalStorage();
    const { resolveAccountWorkspaceSelection } = await import("./currentWorkspace");
    const account = {
      id: 3,
      client_id: null,
      client_ids: [],
      project_id: null,
      project_ids: [],
      nickname: "未绑定账号",
      platform: "douyin" as const,
      group_id: null,
      status: "active" as const,
      external_account_id: "douyin-3",
      integration_status: "connected" as const,
      auth_status: "authorized" as const,
      data_sync_status: "pending" as const,
      created_at: "2026-07-23T00:00:00Z",
    };

    expect(resolveAccountWorkspaceSelection(account, {
      clientId: 2,
      projectId: 1,
      platform: "douyin",
      accountId: null,
    })).toEqual({
      clientId: 2,
      projectId: 1,
      platform: "douyin",
      accountId: 3,
    });
  });

  it("uses an account's explicit default assignments when they exist", async () => {
    installLocalStorage();
    const { resolveAccountWorkspaceSelection } = await import("./currentWorkspace");
    const account = {
      id: 8,
      client_id: 6,
      client_ids: [4, 6],
      project_id: 12,
      project_ids: [10, 12],
      nickname: "已归属账号",
      platform: "douyin" as const,
      group_id: null,
      status: "active" as const,
      external_account_id: "douyin-8",
      integration_status: "connected" as const,
      auth_status: "authorized" as const,
      data_sync_status: "pending" as const,
      created_at: "2026-07-23T00:00:00Z",
    };

    expect(resolveAccountWorkspaceSelection(account, {
      clientId: 2,
      projectId: 1,
      platform: "douyin",
      accountId: 3,
    })).toEqual({
      clientId: 6,
      projectId: 12,
      platform: "douyin",
      accountId: 8,
    });
  });

  it("never falls back to the first account when no account is selected", async () => {
    installLocalStorage();
    const { resolveWorkspaceAccount } = await import("./currentWorkspace");
    const accounts = [
      {
        id: 1,
        nickname: "账号一",
        platform: "douyin" as const,
        group_id: null,
        project_id: null,
        status: "active" as const,
        external_account_id: null,
        integration_status: "connected" as const,
        auth_status: "authorized" as const,
        data_sync_status: "pending" as const,
        created_at: "2026-07-16T00:00:00Z",
      },
    ];

    expect(resolveWorkspaceAccount(accounts, "douyin", null)).toBeNull();
    expect(resolveWorkspaceAccount(accounts, "douyin", 999)).toBeNull();
  });

  it("rejects inactive accounts and accounts from another platform", async () => {
    installLocalStorage();
    const { resolveWorkspaceAccount } = await import("./currentWorkspace");
    const base = {
      nickname: "账号",
      group_id: null,
      project_id: null,
      external_account_id: null,
      integration_status: "connected" as const,
      auth_status: "authorized" as const,
      data_sync_status: "pending" as const,
      created_at: "2026-07-16T00:00:00Z",
    };

    expect(resolveWorkspaceAccount([
      { ...base, id: 1, platform: "douyin", status: "inactive" },
    ], "douyin", 1)).toBeNull();
    expect(resolveWorkspaceAccount([
      { ...base, id: 2, platform: "xiaohongshu", status: "active" },
    ], "douyin", 2)).toBeNull();
  });
});
