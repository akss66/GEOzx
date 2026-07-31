// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AccountDataImportBatchSummary } from "../../api/accountData";
import { ImportBatchHistory } from "./ImportBatchHistory";

function buildSummary(
  status: AccountDataImportBatchSummary["status"],
): AccountDataImportBatchSummary {
  return {
    id: 81,
    status,
    source_kind: "platform_export",
    template_code: "douyin_work_list_v1",
    row_count: 1,
    period_start: "2026-07-01",
    period_end: "2026-07-22",
    committed_at: status === "committed" ? "2026-07-22T08:25:00Z" : null,
    revoked_at: status === "revoked" ? "2026-07-22T08:45:00Z" : null,
    created_by_id: 1,
    created_by_name: "Operator",
    created_at: "2026-07-22T08:00:00Z",
  };
}

function renderHistory(status: AccountDataImportBatchSummary["status"]) {
  return render(
    <ImportBatchHistory
      items={[buildSummary(status)]}
      detailsById={new Map()}
      activeBatchId={null}
      revokingBatchId={null}
      deletingBatchId={null}
      revokeError={null}
      onOpenBatch={vi.fn()}
      onDownloadArtifact={vi.fn()}
      onRevoke={vi.fn()}
      onDelete={vi.fn()}
    />,
  );
}

describe("ImportBatchHistory", () => {
  afterEach(() => cleanup());

  it.each(["uploaded", "preview_ready", "failed", "revoked", "committed"] as const)(
    "offers permanent deletion for %s batches",
    (status) => {
      renderHistory(status);

      fireEvent.click(screen.getByRole("button", { name: "更多操作 批次 81" }));
      expect(screen.getByRole("menuitem", { name: /永久删除/ })).toBeEnabled();
    },
  );

  it("warns that a committed batch will be revoked before permanent deletion", () => {
    renderHistory("committed");

    fireEvent.click(screen.getByRole("button", { name: "更多操作 批次 81" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /永久删除/ }));

    expect(
      screen.getByText("将先撤销该批次产生的数据，再永久删除原文件和历史记录。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认永久删除批次 81" })).toBeEnabled();
  });
});
