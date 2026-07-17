import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import {
  getUserAccessCatalog,
  getUserDetail,
  updateUser,
  updateUserAccess,
} from "./auth";

vi.mock("./client", () => ({
  api: { get: vi.fn(), patch: vi.fn(), put: vi.fn(), post: vi.fn() },
}));

const apiGet = api.get as unknown as Mock;
const apiPatch = api.patch as unknown as Mock;
const apiPut = api.put as unknown as Mock;

describe("user management api", () => {
  beforeEach(() => vi.resetAllMocks());

  it("loads user detail and the access catalog", async () => {
    apiGet.mockResolvedValue({ data: {} });

    await getUserDetail(7);
    await getUserAccessCatalog();

    expect(apiGet).toHaveBeenNthCalledWith(1, "/users/7");
    expect(apiGet).toHaveBeenNthCalledWith(2, "/users/access-catalog");
  });

  it("updates user identity and replaces workspace access", async () => {
    apiPatch.mockResolvedValue({ data: {} });
    apiPut.mockResolvedValue({ data: {} });
    const access = {
      clients: [{ client_id: 3, role: "operator" as const }],
      projects: [{ project_id: 8, role: "reviewer" as const }],
    };

    await updateUser(7, { display_name: "运营同事", is_active: false });
    await updateUserAccess(7, access);

    expect(apiPatch).toHaveBeenCalledWith("/users/7", {
      display_name: "运营同事",
      is_active: false,
    });
    expect(apiPut).toHaveBeenCalledWith("/users/7/access", access);
  });
});
