import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TOKEN_KEY } from "../api/client";
import { installLocalStorage } from "../test/storage";
import type { User } from "../types";

const user: User = {
  id: 1,
  email: "admin@example.com",
  display_name: "Admin",
  role: "admin",
  is_active: true,
};

describe("useAuth", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("initializes token from storage", async () => {
    installLocalStorage({ [TOKEN_KEY]: "existing-token" });

    const { useAuth } = await import("./auth");

    expect(useAuth.getState().token).toBe("existing-token");
    expect(useAuth.getState().user).toBeNull();
  });

  it("persists token and user when auth is set", async () => {
    const storage = installLocalStorage();
    const { useAuth } = await import("./auth");

    useAuth.getState().setAuth("new-token", user);

    expect(storage.setItem).toHaveBeenCalledWith(TOKEN_KEY, "new-token");
    expect(useAuth.getState().token).toBe("new-token");
    expect(useAuth.getState().user).toEqual(user);
  });

  it("clears stored token and user on logout", async () => {
    const storage = installLocalStorage({ [TOKEN_KEY]: "existing-token" });
    const { useAuth } = await import("./auth");

    useAuth.getState().setUser(user);
    useAuth.getState().logout();

    expect(storage.removeItem).toHaveBeenCalledWith(TOKEN_KEY);
    expect(useAuth.getState().token).toBeNull();
    expect(useAuth.getState().user).toBeNull();
  });
});
