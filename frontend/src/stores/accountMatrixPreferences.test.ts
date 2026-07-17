import { beforeEach, describe, expect, it, vi } from "vitest";

import { installLocalStorage } from "../test/storage";

describe("accountMatrixPreferences", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("uses the dense table as the default account workbench", async () => {
    installLocalStorage();
    const { loadAccountMatrixPreferences } = await import("./accountMatrixPreferences");

    expect(loadAccountMatrixPreferences()).toEqual({
      view: "table",
      projectId: null,
      dimension: "all",
      platform: "all",
      groupId: null,
    });
  });

  it("persists the selected view and filters", async () => {
    const storage = installLocalStorage();
    const { loadAccountMatrixPreferences, saveAccountMatrixPreferences } = await import(
      "./accountMatrixPreferences"
    );

    saveAccountMatrixPreferences({
      view: "projects",
      projectId: 12,
      dimension: "persona",
      platform: "douyin",
      groupId: 7,
    });

    expect(loadAccountMatrixPreferences()).toEqual({
      view: "projects",
      projectId: 12,
      dimension: "persona",
      platform: "douyin",
      groupId: 7,
    });
    expect(storage.setItem).toHaveBeenCalledWith(
      "tongzhouxing_account_matrix_preferences",
      JSON.stringify({
        version: 1,
        view: "projects",
        projectId: 12,
        dimension: "persona",
        platform: "douyin",
        groupId: 7,
      }),
    );
  });

  it("ignores unsupported persisted values", async () => {
    installLocalStorage({
      tongzhouxing_account_matrix_preferences: JSON.stringify({
        version: 1,
        view: "unknown",
        projectId: "bad",
        dimension: "invalid",
        platform: "other",
        groupId: -1,
      }),
    });
    const { loadAccountMatrixPreferences } = await import("./accountMatrixPreferences");

    expect(loadAccountMatrixPreferences()).toEqual({
      view: "table",
      projectId: null,
      dimension: "all",
      platform: "all",
      groupId: null,
    });
  });
});
