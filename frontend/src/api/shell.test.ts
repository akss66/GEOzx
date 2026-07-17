import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "./client";
import {
  getUnreadNotificationCount,
  getWorkspaceContext,
  listNotifications,
  markNotificationRead,
  searchWorkspace,
} from "./shell";

vi.mock("./client", () => ({ api: { get: vi.fn(), patch: vi.fn() } }));

const apiGet = api.get as unknown as Mock;
const apiPatch = api.patch as unknown as Mock;

describe("shell api", () => {
  beforeEach(() => vi.resetAllMocks());

  it("loads a scoped workspace context", async () => {
    const context = {
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [],
    };
    apiGet.mockResolvedValueOnce({ data: context });

    await expect(getWorkspaceContext(4, 7)).resolves.toEqual(context);
    expect(apiGet).toHaveBeenCalledWith("/workspace-context", {
      params: { client_id: 4, project_id: 7 },
    });
  });

  it("searches the authorized workspace", async () => {
    const results = [{ kind: "account", id: 3, title: "Primary account", path: "/accounts" }];
    apiGet.mockResolvedValueOnce({ data: results });

    await expect(searchWorkspace("Primary")).resolves.toEqual(results);
    expect(apiGet).toHaveBeenCalledWith("/search", { params: { q: "Primary" } });
  });

  it("loads notification state and marks an item read", async () => {
    const notice = {
      id: 9,
      type: "task.completed",
      title: "Task completed",
      body: null,
      path: "/tasks",
      read_at: null,
      created_at: "2026-07-16T10:00:00Z",
    };
    apiGet
      .mockResolvedValueOnce({ data: [notice] })
      .mockResolvedValueOnce({ data: { count: 1 } });
    apiPatch.mockResolvedValueOnce({ data: { ...notice, read_at: "2026-07-16T10:01:00Z" } });

    await expect(listNotifications()).resolves.toEqual([notice]);
    await expect(getUnreadNotificationCount()).resolves.toBe(1);
    await expect(markNotificationRead(9)).resolves.toMatchObject({ id: 9 });
    expect(apiPatch).toHaveBeenCalledWith("/notifications/9/read");
  });
});
