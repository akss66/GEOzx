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

  it("persists account changes", async () => {
    const storage = installLocalStorage();
    const { useCurrentWorkspace } = await import("./currentWorkspace");

    useCurrentWorkspace.getState().setAccountId(9);

    expect(useCurrentWorkspace.getState().accountId).toBe(9);
    expect(storage.setItem).toHaveBeenCalledWith(
      "tongzhouxing_current_workspace",
      JSON.stringify({ platform: "douyin", accountId: 9 }),
    );
  });
});
