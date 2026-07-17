import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import {
  checkPublishReadiness,
  createDeliverableRevision,
  createContentItem,
  getContentWorkspace,
  listPublishCapabilities,
  listContentItems,
  listDeliverableHistory,
} from "./orchestrator";
import { api } from "./client";

vi.mock("./client", () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const apiGet = api.get as unknown as Mock;
const apiPost = api.post as unknown as Mock;

describe("orchestrator api", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("calls content item endpoints", async () => {
    apiGet.mockResolvedValueOnce({ data: [] });
    apiPost.mockResolvedValueOnce({ data: { id: 1 } });
    apiGet.mockResolvedValueOnce({ data: [] });

    await expect(listContentItems(9)).resolves.toEqual([]);
    await expect(createContentItem({ project_id: 9, title: "Title" })).resolves.toEqual({
      id: 1,
    });
    await expect(listDeliverableHistory(1)).resolves.toEqual([]);

    expect(apiGet).toHaveBeenNthCalledWith(1, "/content-items", {
      params: { project_id: 9 },
    });
    expect(apiPost).toHaveBeenCalledWith("/content-items", {
      project_id: 9,
      title: "Title",
    });
    expect(apiGet).toHaveBeenNthCalledWith(2, "/content-items/1/deliverables");
  });

  it("loads the aggregate workspace and creates a deliverable revision", async () => {
    const workspace = { content_item: { id: 7 }, deliverables: [] };
    const revision = { id: 12, version: 2, payload: { hook: "new" } };
    apiGet.mockResolvedValueOnce({ data: workspace });
    apiPost.mockResolvedValueOnce({ data: revision });

    await expect(getContentWorkspace(7)).resolves.toEqual(workspace);
    await expect(
      createDeliverableRevision(11, { payload: { hook: "new" }, note: "画布修订" }),
    ).resolves.toEqual(revision);

    expect(apiGet).toHaveBeenCalledWith("/content-items/7/workspace");
    expect(apiPost).toHaveBeenCalledWith("/deliverables/11/revisions", {
      payload: { hook: "new" },
      note: "画布修订",
    });
  });

  it("calls publish readiness endpoint", async () => {
    const output = {
      content_item_id: 1,
      platform: "douyin",
      ready: true,
      risk: "pass",
      findings: [],
      tool_call: { id: 99 },
    };
    apiPost.mockResolvedValueOnce({ data: output });

    await expect(
      checkPublishReadiness(1, {
        platform: "douyin",
        title: "Title",
        body: "Body",
      topics: ["agent"],
      material_ids: [7],
      cover_material_id: 8,
      visibility: "public",
      allow_comment: true,
    }),
    ).resolves.toEqual(output);

    expect(apiPost).toHaveBeenCalledWith("/content-items/1/publish-readiness", {
      platform: "douyin",
      title: "Title",
      body: "Body",
      topics: ["agent"],
      material_ids: [7],
      cover_material_id: 8,
      visibility: "public",
      allow_comment: true,
    });
  });

  it("calls publish capabilities endpoint", async () => {
    const output = [
      {
        platform: "douyin",
        content_types: ["video", "image_text"],
        supported_fields: ["title", "scheduled_at"],
        execution_mode: "manual_checklist",
        permission_status: "prepare_only",
        browser_runner_enabled: false,
      },
    ];
    apiGet.mockResolvedValueOnce({ data: output });

    await expect(listPublishCapabilities()).resolves.toEqual(output);

    expect(apiGet).toHaveBeenCalledWith("/publish-capabilities");
  });
});
