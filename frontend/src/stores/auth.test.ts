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
    const storage = installLocalStorage({
      tongzhouxing_brain_active_tasks: '{"version":1,"accounts":{"3":12}}',
      tongzhouxing_brain_active_conversation_threads: '{"version":1,"accounts":{"3":81}}',
    });
    const { queryClient } = await import("../queryClient");
    const { useAuth } = await import("./auth");
    queryClient.setQueryData(["brain-conversation", 81], { id: 81 });

    useAuth.getState().setAuth("new-token", user);

    expect(storage.setItem).toHaveBeenCalledWith(TOKEN_KEY, "new-token");
    expect(storage.removeItem).toHaveBeenCalledWith("tongzhouxing_brain_active_tasks");
    expect(storage.removeItem).toHaveBeenCalledWith(
      "tongzhouxing_brain_active_conversation_threads",
    );
    expect(queryClient.getQueryData(["brain-conversation", 81])).toBeUndefined();
    expect(useAuth.getState().token).toBe("new-token");
    expect(useAuth.getState().user).toEqual(user);
  });

  it("clears stored token and user on logout", async () => {
    const storage = installLocalStorage({
      [TOKEN_KEY]: "existing-token",
      tongzhouxing_brain_active_tasks: '{"version":1,"accounts":{"3":12}}',
      tongzhouxing_brain_active_conversation_threads: '{"version":1,"accounts":{"3":81}}',
    });
    const { queryClient } = await import("../queryClient");
    const { useAuth } = await import("./auth");
    queryClient.setQueryData(["brain-conversation", 81], { id: 81 });

    useAuth.getState().setUser(user);
    useAuth.getState().logout();

    expect(storage.removeItem).toHaveBeenCalledWith(TOKEN_KEY);
    expect(storage.removeItem).toHaveBeenCalledWith("tongzhouxing_brain_active_tasks");
    expect(storage.removeItem).toHaveBeenCalledWith(
      "tongzhouxing_brain_active_conversation_threads",
    );
    expect(queryClient.getQueryData(["brain-conversation", 81])).toBeUndefined();
    expect(useAuth.getState().token).toBeNull();
    expect(useAuth.getState().user).toBeNull();
  });
});
