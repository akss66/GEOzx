// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { PublishHandoff, PublishJob } from "../../types/publishing";
import { PublishJobPanel } from "./PublishJobPanel";

vi.mock("qrcode", () => ({
  default: {
    toDataURL: vi.fn().mockResolvedValue("data:image/png;base64,qr"),
  },
}));

afterEach(cleanup);

const baseJob: PublishJob = {
  id: 21,
  org_id: 1,
  account_id: 3,
  active_client_id: null,
  active_project_id: null,
  created_by_id: 1,
  brain_task_id: 7,
  tool_call_id: 12,
  platform_content_record_id: null,
  platform: "douyin",
  status: "task_created",
  idempotency_key: "publish-job-21",
  publish_package: {
    platform: "douyin",
    account_id: 3,
    content_type: "video",
    title: "一条真实投稿",
    body: "正文",
    topics: ["品牌案例"],
    scheduled_at: null,
    material_ids: [8],
    cover_material_id: null,
    visibility: "public",
    allow_comment: true,
    execution_mode: "official_api",
    manual_steps: [],
  },
  capabilities_snapshot: {},
  approval_snapshot: { approved: true },
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
};

function setup(overrides?: Partial<PublishJob>) {
  const job = { ...baseJob, ...overrides };
  const waitingJob = { ...job, status: "waiting_bind" as const };
  const handoff: PublishHandoff = {
    job: { ...job, status: "handoff_ready" },
    schema_url: "snssdk1128://openplatform/share?share_id=share-1",
    expires_at: "2026-07-27T01:00:00Z",
  };
  const prepareHandoff = vi.fn().mockResolvedValue(handoff);
  const markLaunched = vi.fn().mockResolvedValue(waitingJob);
  const retry = vi.fn().mockResolvedValue({ ...job, status: "task_created" });
  const cancel = vi.fn().mockResolvedValue({ ...job, status: "cancelled" });
  const onJobChange = vi.fn();
  const openSchema = vi.fn();

  render(
    <PublishJobPanel
      job={job}
      onJobChange={onJobChange}
      prepareHandoff={prepareHandoff}
      markLaunched={markLaunched}
      retryJob={retry}
      cancelJob={cancel}
      openSchema={openSchema}
    />,
  );
  return { prepareHandoff, markLaunched, retry, cancel, onJobChange, openSchema };
}

describe("PublishJobPanel", () => {
  it("starts official Douyin handoff only after an explicit user action", async () => {
    const actions = setup();

    fireEvent.click(screen.getByRole("button", { name: /生成抖音投稿二维码/ }));

    await waitFor(() => expect(actions.prepareHandoff).toHaveBeenCalledWith(21));
    expect(actions.markLaunched).not.toHaveBeenCalled();
    expect(await screen.findByAltText("抖音投稿二维码")).toBeInTheDocument();
    expect(screen.getByText("请使用抖音客户端扫码完成投稿")).toBeInTheDocument();
    expect(actions.onJobChange).toHaveBeenCalledWith(
      expect.objectContaining({ status: "handoff_ready" }),
    );
  });

  it("keeps a pending package behind the approval gate", () => {
    setup({ status: "pending_approval" });

    expect(screen.getByText("等待人工审批")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "生成抖音投稿二维码" }),
    ).not.toBeInTheDocument();
  });

  it("offers retry for a failed official handoff", async () => {
    const actions = setup({
      status: "failed",
      last_error_message: "抖音临时不可用",
    });

    expect(screen.getByText("抖音临时不可用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重新准备/ }));
    await waitFor(() => expect(actions.retry).toHaveBeenCalledWith(21));
  });

  it("shows the confirmed official work binding", () => {
    setup({
      status: "bound",
      external_video_id: "video-42",
      bound_at: "2026-07-27T00:20:00Z",
    });

    expect(screen.getByText("作品已绑定")).toBeInTheDocument();
    expect(screen.getByText("官方回流已建立")).toBeInTheDocument();
  });

  it("shows the platform-safe reason when official publishing is disabled", async () => {
    const actions = setup();
    actions.prepareHandoff.mockRejectedValueOnce({
      response: {
        status: 503,
        data: {
          error: {
            code: "DOUYIN_H5_PUBLISH_DISABLED",
            message: "抖音官方投稿尚未在当前环境启用。",
          },
        },
      },
    });

    fireEvent.click(screen.getByRole("button", { name: /生成抖音投稿二维码/ }));

    expect(
      await screen.findByText("抖音官方投稿尚未在当前环境启用。"),
    ).toBeInTheDocument();
  });

  it("uses the same official schema for QR handoff and direct Douyin launch", async () => {
    const actions = setup();

    fireEvent.click(screen.getByRole("button", { name: /生成抖音投稿二维码/ }));
    await screen.findByAltText("抖音投稿二维码");

    fireEvent.click(screen.getByRole("button", { name: /尝试直接打开抖音/ }));

    expect(actions.openSchema).toHaveBeenCalledWith(
      "snssdk1128://openplatform/share?share_id=share-1",
    );
    await waitFor(() => expect(actions.markLaunched).toHaveBeenCalledWith(21));
    expect(actions.onJobChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: "waiting_bind" }),
    );
  });
});
