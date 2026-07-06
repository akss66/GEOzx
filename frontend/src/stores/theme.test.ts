import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installLocalStorage } from "../test/storage";

describe("useThemeMode", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("defaults to light mode", async () => {
    installLocalStorage();

    const { useThemeMode } = await import("./theme");

    expect(useThemeMode.getState().mode).toBe("light");
  });

  it("initializes from stored mode", async () => {
    installLocalStorage({ dyflow_theme: "light" });

    const { useThemeMode } = await import("./theme");

    expect(useThemeMode.getState().mode).toBe("light");
  });

  it("toggles and persists the selected mode", async () => {
    const storage = installLocalStorage();
    const { useThemeMode } = await import("./theme");

    useThemeMode.getState().toggle();

    expect(useThemeMode.getState().mode).toBe("dark");
    expect(storage.setItem).toHaveBeenCalledWith("dyflow_theme", "dark");

    useThemeMode.getState().toggle();

    expect(useThemeMode.getState().mode).toBe("light");
    expect(storage.setItem).toHaveBeenCalledWith("dyflow_theme", "light");
  });
});
