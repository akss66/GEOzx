// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installLocalStorage } from "../test/storage";
import { api, TOKEN_KEY } from "./client";

function rejectWith(response: { status: number; data?: unknown }) {
  return api.get("/test", {
    adapter: async () => Promise.reject({ response }),
  });
}

describe("api response authentication handling", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("preserves the session token for a structured business 401", async () => {
    const storage = installLocalStorage({ [TOKEN_KEY]: "active-session" });

    await expect(rejectWith({
      status: 401,
      data: {
        detail: {
          code: "SECONDARY_PASSWORD_INVALID",
          message: "Invalid secondary password",
        },
      },
    })).rejects.toMatchObject({ response: { status: 401 } });

    expect(storage.getItem(TOKEN_KEY)).toBe("active-session");
    expect(storage.removeItem).not.toHaveBeenCalled();
  });

  it("clears the session token for an ordinary authentication 401", async () => {
    const storage = installLocalStorage({ [TOKEN_KEY]: "expired-session" });

    await expect(rejectWith({
      status: 401,
      data: { detail: "Invalid or expired token" },
    })).rejects.toMatchObject({ response: { status: 401 } });

    expect(storage.getItem(TOKEN_KEY)).toBeNull();
    expect(storage.removeItem).toHaveBeenCalledWith(TOKEN_KEY);
  });
});
