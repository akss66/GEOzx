// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { listPublishJobs } from "../../services/publishing";
import type { PublishJob } from "../../types/publishing";
import { PublishExecutionQueue } from "./PublishExecutionQueue";

vi.mock("../../services/publishing", () => ({
  listPublishJobs: vi.fn(),
}));

vi.mock("../content/PublishJobPanel", () => ({
  PublishJobPanel: ({ job }: { job: PublishJob }) => <div>任务 #{job.id}</div>,
}));

describe("PublishExecutionQueue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows approved publish jobs for the current account", async () => {
    vi.mocked(listPublishJobs).mockResolvedValue([
      makeJob({ id: 7, status: "task_created" }),
      makeJob({ id: 8, status: "pending_approval" }),
    ]);

    renderQueue(3);

    expect(await screen.findByText("任务 #7")).toBeInTheDocument();
    expect(screen.queryByText("任务 #8")).not.toBeInTheDocument();
    expect(listPublishJobs).toHaveBeenCalledWith({ accountId: 3, limit: 50 });
  });

  it("stays hidden when no account is selected", () => {
    renderQueue(null);

    expect(screen.queryByRole("heading", { name: "待发布任务" })).not.toBeInTheDocument();
    expect(listPublishJobs).not.toHaveBeenCalled();
  });
});

function renderQueue(accountId: number | null) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <PublishExecutionQueue accountId={accountId} />
    </QueryClientProvider>,
  );
}

function makeJob(overrides: Partial<PublishJob> = {}): PublishJob {
  return {
    id: 7,
    org_id: 1,
    account_id: 3,
    active_client_id: null,
    active_project_id: null,
    created_by_id: 1,
    brain_task_id: null,
    tool_call_id: 4,
    platform_content_record_id: null,
    platform: "douyin",
    status: "task_created",
    idempotency_key: "content:1:tool:4",
    publish_package: {
      platform: "douyin",
      account_id: 3,
      content_type: "video",
      title: "标题",
      body: "",
      topics: [],
      scheduled_at: null,
      material_ids: [1],
      cover_material_id: null,
      visibility: "public",
      allow_comment: true,
      execution_mode: "official_api",
      manual_steps: [],
    },
    capabilities_snapshot: {},
    approval_snapshot: {},
    share_id: null,
    posting_task_id: null,
    external_video_id: null,
    external_item_id: null,
    expires_at: null,
    handoff_started_at: null,
    bound_at: null,
    retry_count: 0,
    next_retry_at: null,
    last_error_code: null,
    last_error_message: null,
    last_platform_log_id: null,
    created_at: "2026-07-27T00:00:00Z",
    updated_at: "2026-07-27T00:00:00Z",
    ...overrides,
  };
}
