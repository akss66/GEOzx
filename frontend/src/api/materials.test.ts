import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { listMaterials } from "./materials";
import { api } from "./client";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;

describe("materials api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("lists materials by content item", async () => {
    const rows = [
      {
        id: 1,
        content_item_id: 9,
        deliverable_id: 3,
        kind: "video",
        provider: "fake",
        status: "ready",
        size_bytes: 1024,
        file_url: "/materials/1/file",
        error: null,
        created_at: "2026-07-03T00:00:00Z",
      },
    ];
    apiGet.mockResolvedValueOnce({ data: rows });

    await expect(listMaterials({ contentItemId: 9 })).resolves.toEqual(rows);

    expect(apiGet).toHaveBeenCalledWith("/materials", {
      params: { content_item_id: 9 },
    });
  });
});
