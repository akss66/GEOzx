// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type AccountDataImportBatch,
  type AccountDataImportBatchSummary,
  type AccountDataStatus,
  commitAccountDataImportBatch,
  getAccountDataImportBatch,
  getAccountDataStatus,
  listAccountDataImports,
  resolveAccountDataImportRow,
  revokeAccountDataImportBatch,
  uploadAccountDataImport,
} from "../api/accountData";
import { getWorkspaceContext } from "../api/shell";
import AccountDataCenter from "./AccountDataCenter";

const workspaceMocks = vi.hoisted(() => ({
  clientId: 1 as number | null,
  projectId: 11 as number | null,
  platform: "douyin" as const,
  accountId: 7 as number | null,
  setPlatform: vi.fn(),
  setAccountId: vi.fn(),
}));

vi.mock("../api/accountData", () => ({
  getAccountDataStatus: vi.fn(),
  listAccountDataImports: vi.fn(),
  getAccountDataImportBatch: vi.fn(),
  uploadAccountDataImport: vi.fn(),
  resolveAccountDataImportRow: vi.fn(),
  commitAccountDataImportBatch: vi.fn(),
  revokeAccountDataImportBatch: vi.fn(),
}));

vi.mock("../api/shell", () => ({
  getWorkspaceContext: vi.fn(),
}));

vi.mock("../stores/currentWorkspace", () => ({
  useCurrentWorkspace: vi.fn((selector?: (state: typeof workspaceMocks) => unknown) =>
    typeof selector === "function" ? selector(workspaceMocks) : workspaceMocks,
  ),
}));

function buildAccount(id = 42) {
  return {
    id,
    nickname: "数码菌",
    platform: "douyin" as const,
    group_id: null,
    project_id: 11,
    status: "active" as const,
    external_account_id: "douyin-42",
    integration_status: "connected" as const,
    auth_status: "authorized" as const,
    data_sync_status: "healthy" as const,
    avatar_url: "https://cdn.example.com/avatar.png",
    risk_count: 1,
    created_at: "2026-07-22T00:00:00Z",
  };
}

function buildStatus(): AccountDataStatus {
  return {
    account_id: 42,
    latest_confirmed_at: "2026-07-22T08:10:00Z",
    coverage: {
      account_metrics: "available",
      content_metrics: "partial",
      benchmarks: "missing",
    },
    sources: [
      {
        batch_id: 81,
        source_kind: "platform_export",
        template_code: "douyin_work_list_v1",
        data_domain: "content_metrics",
        committed_at: "2026-07-22T08:10:00Z",
        period_start: "2026-07-01",
        period_end: "2026-07-22",
      },
    ],
  };
}

function buildPreviewBatch(): AccountDataImportBatch {
  return {
    id: 81,
    status: "preview_ready",
    source_kind: "platform_export",
    template_code: "douyin_work_list_v1",
    row_count: 1,
    period_start: "2026-07-01",
    period_end: "2026-07-22",
    committed_at: null,
    revoked_at: null,
    created_at: "2026-07-22T08:00:00Z",
    artifacts: [
      {
        id: 501,
        filename: "works.xlsx",
        content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        byte_size: 2048,
        sha256: "a".repeat(64),
        download_url: "/account-data/42/imports/81/artifacts/501",
      },
    ],
    conflicts: [
      {
        id: 701,
        row_number: 2,
        status: "open",
        field_name: "platform_content_record_id",
        conflict_code: "ambiguous_match",
        message: "同一发布时间存在多个候选作品，请人工确认。",
        candidate_content_ids: [91, 92],
        resolved_by_id: null,
        resolved_at: null,
      },
    ],
    rows: [
      {
        id: 601,
        row_number: 2,
        status: "needs_resolution",
        raw_values: { title: "作品 A", published_at: "2026-07-18 14:11:20" },
        normalized_values: { title: "作品 A", published_at: "2026-07-18T14:11:20" },
        field_errors: [],
        warnings: [],
        candidate_content_ids: [91, 92],
        projected_target_ids: [],
        platform_content_record_id: null,
        resolution_outcome: null,
        resolved_by_id: null,
        resolved_at: null,
      },
    ],
  };
}

function renderPage(route = "/accounts/42/data") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <MemoryRouter initialEntries={[route]}>
      <QueryClientProvider client={queryClient}>
        <AntApp>
          <Routes>
            <Route path="/accounts/:accountId/data" element={<AccountDataCenter />} />
          </Routes>
        </AntApp>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("AccountDataCenter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn(() => ({
        matches: false,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  afterEach(cleanup);

  it("shows an explicit account error instead of silently falling back", async () => {
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount(7)],
    });

    renderPage();

    expect(await screen.findByText("找不到当前账号的数据中心")).toBeInTheDocument();
    expect(screen.getByText("系统不会自动切换到其他账号。")).toBeInTheDocument();
    expect(getAccountDataStatus).not.toHaveBeenCalled();
    expect(workspaceMocks.setAccountId).not.toHaveBeenCalledWith(7);
  });

  it("shows a recoverable unknown-template error inline", async () => {
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({ items: [] });
    vi.mocked(uploadAccountDataImport).mockRejectedValueOnce({
      response: {
        status: 422,
        data: { detail: "Unknown or unsupported template" },
      },
    });

    renderPage();

    const fileInput = await screen.findByLabelText("选择导入文件");
    fireEvent.change(fileInput, {
      target: {
        files: [
          new File(["bad"], "unknown.xlsx", {
            type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          }),
        ],
      },
    });

    expect(await screen.findByText("无法识别导入模板")).toBeInTheDocument();
    expect(screen.getByText("请改用已支持的抖音导出模板后重新上传。")).toBeInTheDocument();
  });

  it("blocks commit until one ambiguous row is resolved", async () => {
    const preview = buildPreviewBatch();
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({
      items: [
        {
          id: 81,
          status: "preview_ready",
          source_kind: "platform_export",
          template_code: "douyin_work_list_v1",
          row_count: 1,
          period_start: "2026-07-01",
          period_end: "2026-07-22",
          committed_at: null,
          revoked_at: null,
          created_at: "2026-07-22T08:00:00Z",
        },
      ],
    });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(preview);
    vi.mocked(resolveAccountDataImportRow).mockResolvedValueOnce({
      ...preview.rows[0],
      status: "ready",
      platform_content_record_id: 91,
      resolution_outcome: "selected_existing_content",
      candidate_content_ids: [91, 92],
    });

    renderPage();

    expect(await screen.findByRole("button", { name: "确认导入" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "选用候选作品 #91" }));

    await waitFor(() =>
      expect(resolveAccountDataImportRow).toHaveBeenCalledWith(42, 81, 2, 91),
    );
    expect(screen.getByRole("button", { name: "确认导入" })).toBeEnabled();
  });

  it("commits successfully and refreshes status plus history", async () => {
    const preview = buildPreviewBatch();
    const committed: AccountDataImportBatch = {
      ...preview,
      status: "committed",
      committed_at: "2026-07-22T08:25:00Z",
      conflicts: [],
      rows: preview.rows.map((row) => ({
        ...row,
        status: "committed" as const,
        platform_content_record_id: 91,
      })),
    };
    const pendingStatus: AccountDataStatus = {
      ...buildStatus(),
      latest_confirmed_at: null,
    };
    const refreshedPreview: AccountDataImportBatch = {
      ...preview,
      rows: preview.rows.map((row) => ({ ...row, status: "ready" as const })),
      conflicts: [],
    };
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(getAccountDataStatus)
      .mockResolvedValueOnce(pendingStatus)
      .mockResolvedValueOnce(buildStatus());
    vi.mocked(listAccountDataImports)
      .mockResolvedValueOnce({
        items: [
          {
            id: 81,
            status: "preview_ready",
            source_kind: "platform_export",
            template_code: "douyin_work_list_v1",
            row_count: 1,
            period_start: "2026-07-01",
            period_end: "2026-07-22",
            committed_at: null,
            revoked_at: null,
            created_at: "2026-07-22T08:00:00Z",
          },
        ],
      })
      .mockResolvedValueOnce({
        items: [
          {
            id: 81,
            status: "committed",
            source_kind: "platform_export",
            template_code: "douyin_work_list_v1",
            row_count: 1,
            period_start: "2026-07-01",
            period_end: "2026-07-22",
            committed_at: "2026-07-22T08:25:00Z",
            revoked_at: null,
            created_at: "2026-07-22T08:00:00Z",
          },
        ],
      });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce(refreshedPreview);
    vi.mocked(commitAccountDataImportBatch).mockResolvedValueOnce(committed);

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "确认导入" }));

    await waitFor(() =>
      expect(commitAccountDataImportBatch).toHaveBeenCalledWith(42, 81),
    );
    await waitFor(() => expect(listAccountDataImports).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getAccountDataStatus).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("最近确认时间")).toBeInTheDocument();
    expect((await screen.findAllByText("已确认")).length).toBeGreaterThan(0);
  });

  it("requires an explicit revoke confirmation before revoking one batch", async () => {
    const committed: AccountDataImportBatchSummary = {
      id: 81,
      status: "committed",
      source_kind: "platform_export",
      template_code: "douyin_work_list_v1",
      row_count: 1,
      period_start: "2026-07-01",
      period_end: "2026-07-22",
      committed_at: "2026-07-22T08:25:00Z",
      revoked_at: null,
      created_at: "2026-07-22T08:00:00Z",
    };
    vi.mocked(getWorkspaceContext).mockResolvedValueOnce({
      clients: [],
      selected_client: null,
      projects: [],
      selected_project: null,
      accounts: [buildAccount()],
    });
    vi.mocked(listAccountDataImports)
      .mockResolvedValueOnce({ items: [committed] })
      .mockResolvedValueOnce({
        items: [
          {
            ...committed,
            status: "revoked",
            revoked_at: "2026-07-22T08:45:00Z",
          },
        ],
      });
    vi.mocked(getAccountDataImportBatch)
      .mockResolvedValueOnce({
        ...buildPreviewBatch(),
        status: "committed",
        committed_at: committed.committed_at,
        conflicts: [],
        rows: buildPreviewBatch().rows.map((row) => ({
          ...row,
          status: "committed" as const,
          platform_content_record_id: 91,
        })),
      })
      .mockResolvedValueOnce({
        ...buildPreviewBatch(),
        status: "revoked",
        committed_at: committed.committed_at,
        revoked_at: "2026-07-22T08:45:00Z",
        rows: buildPreviewBatch().rows.map((row) => ({
          ...row,
          status: "revoked" as const,
          platform_content_record_id: 91,
        })),
      });
    vi.mocked(getAccountDataStatus)
      .mockResolvedValueOnce(buildStatus())
      .mockResolvedValueOnce(buildStatus());
    vi.mocked(revokeAccountDataImportBatch).mockResolvedValueOnce({
      ...buildPreviewBatch(),
      status: "revoked",
      committed_at: committed.committed_at,
      revoked_at: "2026-07-22T08:45:00Z",
    });

    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "撤销批次 81" }));
    expect(screen.getByText("确认撤销这次写入？")).toBeInTheDocument();
    expect(revokeAccountDataImportBatch).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确认撤销批次 81" }));

    await waitFor(() =>
      expect(revokeAccountDataImportBatch).toHaveBeenCalledWith(42, 81),
    );
  });
});
