// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountDataImportBatch, ManualPreviewPayload } from "../../api/accountData";
import { ManualDataEntry } from "./ManualDataEntry";

function buildScreenshotBatch(): AccountDataImportBatch {
  return {
    id: 91,
    status: "preview_ready",
    source_kind: "screenshot_verified",
    template_code: "manual_account_period_v1",
    row_count: 1,
    period_start: "2026-07-15",
    period_end: "2026-07-21",
    committed_at: null,
    revoked_at: null,
    created_by_id: 1,
    created_by_name: "Operator",
    created_at: "2026-07-22T08:00:00Z",
    artifacts: [
      {
        id: 201,
        filename: "diagnosis.png",
        content_type: "image/png",
        byte_size: 2048,
        sha256: "a".repeat(64),
        download_url: "/account-data/42/imports/91/artifacts/201",
      },
    ],
    conflicts: [],
    rows: [
      {
        id: 301,
        row_number: 1,
        status: "needs_resolution",
        raw_values: {},
        normalized_values: {
          data_domain: "account_period_totals",
          stat_date: "2026-07-21",
          follower_count: 1280,
          total_play: 578,
        },
        field_errors: [],
        warnings: [],
        candidate_content_ids: [],
        projected_target_ids: [],
        platform_content_record_id: null,
        resolution_outcome: null,
        resolved_by_id: null,
        resolved_at: null,
      },
    ],
  };
}

describe("ManualDataEntry", () => {
  beforeEach(() => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:manual-evidence"),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("creates a structured account-period preview without inventing empty metrics", () => {
    const onPreview = vi.fn<(payload: ManualPreviewPayload, screenshot: File | null) => void>();
    render(
      <ManualDataEntry
        batch={null}
        feedback={null}
        creating={false}
        confirming={false}
        committing={false}
        canCommit={false}
        onPreview={onPreview}
        onConfirmRow={vi.fn()}
        onCommit={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("统计日期"), { target: { value: "2026-07-21" } });
    fireEvent.change(screen.getByLabelText("粉丝总数"), { target: { value: "1280" } });
    fireEvent.change(screen.getByLabelText("播放量"), { target: { value: "578" } });
    fireEvent.click(screen.getByRole("button", { name: "生成录入预览" }));

    expect(onPreview).toHaveBeenCalledWith(
      expect.objectContaining({
        data_domain: "account_period_totals",
        stat_date: "2026-07-21",
        account_metrics: expect.objectContaining({
          follower_count: 1280,
          total_play: 578,
          total_exposure: null,
        }),
      }),
      null,
    );
  });

  it("shows selected screenshot beside the editable fields", () => {
    render(
      <ManualDataEntry
        batch={null}
        feedback={null}
        creating={false}
        confirming={false}
        committing={false}
        canCommit={false}
        onPreview={vi.fn()}
        onConfirmRow={vi.fn()}
        onCommit={vi.fn()}
      />,
    );
    const screenshot = new File(["image"], "diagnosis.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("上传数据截图"), {
      target: { files: [screenshot] },
    });

    expect(screen.getByRole("img", { name: "待核对的数据截图" })).toHaveAttribute(
      "src",
      "blob:manual-evidence",
    );
    expect(screen.getByText("diagnosis.png")).toBeInTheDocument();
  });

  it("requires screenshot confirmation before enabling final import", () => {
    const onConfirmRow = vi.fn();
    const onCommit = vi.fn();
    render(
      <ManualDataEntry
        batch={buildScreenshotBatch()}
        feedback={null}
        creating={false}
        confirming={false}
        committing={false}
        canCommit={false}
        onPreview={vi.fn()}
        onConfirmRow={onConfirmRow}
        onCommit={onCommit}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /确认截图数据/ }));
    expect(onConfirmRow).toHaveBeenCalledWith(1);
    expect(screen.getByRole("button", { name: "确认写入" })).toBeDisabled();
  });

  it("switches to audience entry without hiding the evidence workspace", () => {
    render(
      <ManualDataEntry
        batch={null}
        feedback={null}
        creating={false}
        confirming={false}
        committing={false}
        canCommit={false}
        onPreview={vi.fn()}
        onConfirmRow={vi.fn()}
        onCommit={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "粉丝画像" }));
    expect(screen.getByLabelText("画像维度")).toBeInTheDocument();
    expect(screen.getByText("截图证据（可选）")).toBeInTheDocument();
  });

  it("does not carry a previous domain preview into another manual form", () => {
    render(
      <ManualDataEntry
        batch={buildScreenshotBatch()}
        feedback={{
          tone: "success",
          title: "preview ready",
          description: "confirm before committing",
        }}
        creating={false}
        confirming={false}
        committing={false}
        canCommit={false}
        onPreview={vi.fn()}
        onConfirmRow={vi.fn()}
        onCommit={vi.fn()}
      />,
    );

    expect(screen.getByText("manual_account_period_v1")).toBeInTheDocument();
    expect(screen.getByText("preview ready")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "粉丝画像" }));

    expect(screen.queryByText("manual_account_period_v1")).not.toBeInTheDocument();
    expect(screen.queryByText("preview ready")).not.toBeInTheDocument();
  });
});
