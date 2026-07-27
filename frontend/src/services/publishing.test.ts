import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { api } from "../api/client";
import {
  cancelPublishJob,
  createPublishJob,
  listPublishJobs,
  markPublishJobLaunched,
  preparePublishHandoff,
  retryPublishJob,
} from "./publishing";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

const get = api.get as unknown as Mock;
const post = api.post as unknown as Mock;
const job = { id: 17, account_id: 6, status: "task_created" };

describe("publishing service", () => {
  beforeEach(() => vi.resetAllMocks());

  it("creates and lists durable publish jobs", async () => {
    const input = {
      account_id: 6,
      active_client_id: 2,
      active_project_id: 3,
      tool_call_id: 46,
      idempotency_key: "content:88:tool:46",
      publish_package: {
        platform: "douyin" as const,
        account_id: 6,
        content_type: "video" as const,
        title: "新品发布",
        body: "",
        topics: [],
        scheduled_at: null,
        material_ids: [7],
        cover_material_id: null,
        visibility: "public" as const,
        allow_comment: true,
        execution_mode: "official_api" as const,
        manual_steps: [],
      },
    };
    post.mockResolvedValueOnce({ data: job });
    get.mockResolvedValueOnce({ data: [job] });

    await expect(createPublishJob(input)).resolves.toEqual(job);
    await expect(listPublishJobs({ accountId: 6, limit: 20 })).resolves.toEqual([job]);

    expect(post).toHaveBeenCalledWith("/publishing/jobs", input);
    expect(get).toHaveBeenCalledWith("/publishing/jobs", {
      params: { account_id: 6, limit: 20 },
    });
  });

  it("uses explicit endpoints for handoff and lifecycle actions", async () => {
    post
      .mockResolvedValueOnce({ data: { job, schema_url: "snssdk1128://openplatform/share" } })
      .mockResolvedValueOnce({ data: { ...job, status: "waiting_bind" } })
      .mockResolvedValueOnce({ data: { ...job, status: "task_created" } })
      .mockResolvedValueOnce({ data: { ...job, status: "cancelled" } });

    await preparePublishHandoff(17);
    await markPublishJobLaunched(17);
    await retryPublishJob(17);
    await cancelPublishJob(17);

    expect(post).toHaveBeenNthCalledWith(1, "/publishing/jobs/17/handoff");
    expect(post).toHaveBeenNthCalledWith(2, "/publishing/jobs/17/launched");
    expect(post).toHaveBeenNthCalledWith(3, "/publishing/jobs/17/retry");
    expect(post).toHaveBeenNthCalledWith(4, "/publishing/jobs/17/cancel");
  });
});
