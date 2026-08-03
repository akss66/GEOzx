// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createAccountDataImportJob,
  createManualAccountDataPreview,
  getAccountDataImportBatch,
  getAccountDataStatus,
  listAccountDataImports,
  type AccountDataImportJob,
  type AccountDataStatus,
} from "../api/accountData";
import { getAccount } from "../api/workspace";
import AccountDataCenter from "./AccountDataCenter";

const workspaceMocks = vi.hoisted(() => ({
  clientId: 1 as number | null,
  projectId: 11 as number | null,
  platform: "douyin" as const,
  accountId: 42 as number | null,
  setPlatform: vi.fn(),
  setAccountId: vi.fn(),
  hydrate: vi.fn(),
}));

vi.mock("../api/accountData", async () => {
  const actual = await vi.importActual<typeof import("../api/accountData")>(
    "../api/accountData",
  );
  return {
    ...actual,
    getAccountDataStatus: vi.fn(),
    listAccountDataImports: vi.fn(),
    getAccountDataImportBatch: vi.fn(),
    getAccountDataImportRows: vi.fn(),
    createAccountDataImportJob: vi.fn(),
    getAccountDataImportJob: vi.fn(),
    retryAccountDataImportFile: vi.fn(),
    createManualAccountDataPreview: vi.fn(),
    confirmManualAccountDataRow: vi.fn(),
    commitAccountDataImportBatch: vi.fn(),
    revokeAccountDataImportBatch: vi.fn(),
    deleteAccountDataImportBatch: vi.fn(),
    downloadAccountDataArtifact: vi.fn(),
  };
});

vi.mock("../api/workspace", () => ({
  getAccount: vi.fn(),
}));

vi.mock("../stores/currentWorkspace", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../stores/currentWorkspace")>();
  return {
    ...actual,
    useCurrentWorkspace: vi.fn((selector?: (state: typeof workspaceMocks) => unknown) =>
      typeof selector === "function" ? selector(workspaceMocks) : workspaceMocks,
    ),
  };
});

function buildAccount(id = 42) {
  return {
    id,
    nickname: id === 42 ? "数码菌" : `账号 ${id}`,
    platform: "douyin" as const,
    client_id: 1,
    client_ids: [1],
    group_id: null,
    project_id: 11,
    project_ids: [11],
    status: "active" as const,
    external_account_id: `douyin-${id}`,
    integration_status: "connected" as const,
    auth_status: "authorized" as const,
    data_sync_status: "healthy" as const,
    avatar_url: "https://cdn.example.com/avatar.png",
    risk_count: 0,
    created_at: "2026-07-22T00:00:00Z",
  };
}

function buildStatus(accountId = 42): AccountDataStatus {
  const dailyPlaySource = {
    batch_id: 81,
    source_kind: "platform_export" as const,
    template_code: "douyin_daily_play_v1",
    data_domain: "account_metrics",
    committed_at: "2026-07-31T08:10:00Z",
    period_start: "2026-07-02",
    period_end: "2026-07-31",
  };
  return {
    account_id: accountId,
    latest_confirmed_at: "2026-07-31T08:10:00Z",
    coverage: {
      account_metrics: "available",
      content_metrics: "missing",
      audience_profiles: "missing",
      benchmarks: "missing",
    },
    dataset_inventory: [
      {
        data_domain: "account_metrics",
        status: "available",
        confirmed_period_start: "2026-07-02",
        confirmed_period_end: "2026-07-31",
        latest_source: dailyPlaySource,
      },
      {
        data_domain: "content_metrics",
        status: "not_imported",
        confirmed_period_start: null,
        confirmed_period_end: null,
        latest_source: null,
      },
      {
        data_domain: "audience_profiles",
        status: "not_imported",
        confirmed_period_start: null,
        confirmed_period_end: null,
        latest_source: null,
      },
      {
        data_domain: "benchmarks",
        status: "not_imported",
        confirmed_period_start: null,
        confirmed_period_end: null,
        latest_source: null,
      },
    ],
    sources: [dailyPlaySource],
  };
}

function buildImportJob(accountId = 42): AccountDataImportJob {
  return {
    id: 91,
    account_id: accountId,
    client_request_id: `request-${accountId}`,
    status: "completed_with_errors",
    file_count: 2,
    completed_file_count: 1,
    failed_file_count: 1,
    started_at: "2026-07-31T10:00:00Z",
    completed_at: "2026-07-31T10:00:02Z",
    files: [
      {
        id: 101,
        retry_of_file_id: null,
        ordinal: 1,
        filename: "播放数据.xlsx",
        content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        byte_size: 10,
        sha256: "a".repeat(64),
        status: "completed",
        error_payload: {},
        started_at: null,
        completed_at: null,
        datasets: [
          {
            id: 201,
            template_code: "douyin_daily_play_v1",
            sheet_name: "日播放",
            dataset_ordinal: 1,
            status: "committed",
            row_count: 30,
          },
        ],
      },
      {
        id: 102,
        retry_of_file_id: null,
        ordinal: 2,
        filename: "损坏数据.xlsx",
        content_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        byte_size: 10,
        sha256: "b".repeat(64),
        status: "failed",
        error_payload: { message: "文件结构损坏" },
        started_at: null,
        completed_at: null,
        datasets: [],
      },
    ],
  };
}

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
}

function renderPage(route = "/accounts/42/data") {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={[route]}>
          <Routes>
            <Route path="/accounts/:accountId/data" element={<AccountDataCenter />} />
          </Routes>
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

function AccountSwitcher() {
  const navigate = useNavigate();
  const [accountId, setAccountId] = useState(42);
  return (
    <>
      <button
        type="button"
        onClick={() => {
          const next = accountId === 42 ? 99 : 42;
          setAccountId(next);
          navigate(`/accounts/${next}/data`);
        }}
      >
        切换账号
      </button>
      <Routes>
        <Route path="/accounts/:accountId/data" element={<AccountDataCenter />} />
      </Routes>
    </>
  );
}

function renderSwitcher() {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter initialEntries={["/accounts/42/data"]}>
          <AccountSwitcher />
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("AccountDataCenter", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.clearAllMocks();
    workspaceMocks.clientId = 1;
    workspaceMocks.projectId = 11;
    workspaceMocks.platform = "douyin";
    workspaceMocks.accountId = 42;
    vi.mocked(getAccount).mockImplementation(async (accountId) => buildAccount(accountId));
    vi.mocked(getAccountDataStatus).mockImplementation(async (accountId) =>
      buildStatus(accountId),
    );
    vi.mocked(listAccountDataImports).mockResolvedValue({ items: [] });
  });

  it("rejects an invalid account route before loading account data", async () => {
    renderPage("/accounts/not-a-number/data");

    expect(await screen.findByText("账号地址无效")).toBeInTheDocument();
    expect(getAccount).not.toHaveBeenCalled();
  });

  it("reports each dataset independently and never calls one imported table complete", async () => {
    renderPage();

    expect(
      await screen.findByRole("heading", {
        name: "已导入 1/4 类数据，当前账号已有可用数据",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("账号概览").closest("article")).toHaveTextContent(
      "已有可用数据",
    );
    expect(screen.getByText("作品表现").closest("article")).toHaveTextContent("尚未导入");
    expect(screen.getByText("粉丝画像").closest("article")).toHaveTextContent("尚未导入");
    expect(screen.getByText("对标基准").closest("article")).toHaveTextContent("尚未导入");
    expect(screen.queryByText("数据完整")).not.toBeInTheDocument();
    expect(screen.queryByText("补齐数据")).not.toBeInTheDocument();
  });

  it("opens the multi-file dropzone from the header action", async () => {
    renderPage();

    const addFilesButton = await screen.findByRole("button", {
      name: /添加数据文件/,
    });
    await waitFor(() => expect(addFilesButton).toBeEnabled());
    fireEvent.click(addFilesButton);

    expect(
      await screen.findByRole("group", { name: "拖入账号数据文件" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("account-data-file-input")).toHaveAttribute("multiple");
    expect(screen.queryByText("更换文件")).not.toBeInTheDocument();
  });

  it("submits multiple files together and keeps per-file results visible", async () => {
    vi.mocked(createAccountDataImportJob).mockResolvedValueOnce(buildImportJob());
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "导入与补录" }));
    const files = [
      new File(["daily"], "播放数据.xlsx"),
      new File(["broken"], "损坏数据.xlsx"),
    ];

    fireEvent.change(screen.getByTestId("account-data-file-input"), {
      target: { files },
    });

    await waitFor(() =>
      expect(createAccountDataImportJob).toHaveBeenCalledWith(
        42,
        files,
        expect.any(String),
      ),
    );
    expect(await screen.findByText("播放数据.xlsx")).toBeInTheDocument();
    expect(screen.getByText("损坏数据.xlsx")).toBeInTheDocument();
    expect(screen.getByText("已写入")).toBeInTheDocument();
    expect(screen.getByText("导入失败")).toBeInTheDocument();
  });

  it("keeps manual entry as an explicit secondary path", async () => {
    vi.mocked(createManualAccountDataPreview).mockResolvedValueOnce({
      id: 301,
      status: "preview_ready",
      source_kind: "manual_entry",
      template_code: "manual_account_period_v1",
      row_count: 1,
      period_start: "2026-07-31",
      period_end: "2026-07-31",
      committed_at: null,
      revoked_at: null,
      created_by_id: 1,
      created_by_name: "Operator",
      created_at: "2026-07-31T12:00:00Z",
      artifacts: [],
      rows: [],
      conflicts: [],
    });
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "导入与补录" }));

    fireEvent.click(screen.getByRole("button", { name: "其他补录方式" }));
    fireEvent.click(await screen.findByRole("button", { name: "人工补录" }));

    expect(await screen.findByLabelText("统计日期")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "人工补录" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("does not count a pending legacy batch as confirmed coverage", async () => {
    vi.mocked(getAccountDataStatus).mockResolvedValueOnce({
      ...buildStatus(),
      latest_confirmed_at: null,
      coverage: {
        account_metrics: "missing",
        content_metrics: "missing",
        audience_profiles: "missing",
        benchmarks: "missing",
      },
      dataset_inventory: buildStatus().dataset_inventory?.map((item) => ({
        ...item,
        status: "not_imported" as const,
        confirmed_period_start: null,
        confirmed_period_end: null,
        latest_source: null,
      })),
      sources: [],
    });
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({
      items: [
        {
          id: 81,
          status: "preview_ready",
          source_kind: "platform_export",
          template_code: "douyin_work_list_v1",
          row_count: 30,
          period_start: "2026-07-01",
          period_end: "2026-07-31",
          committed_at: null,
          revoked_at: null,
          created_by_id: 1,
          created_by_name: "Operator",
          created_at: "2026-07-31T08:00:00Z",
        },
      ],
    });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce({
      id: 81,
      status: "preview_ready",
      source_kind: "platform_export",
      template_code: "douyin_work_list_v1",
      row_count: 30,
      period_start: "2026-07-01",
      period_end: "2026-07-31",
      committed_at: null,
      revoked_at: null,
      created_by_id: 1,
      created_by_name: "Operator",
      created_at: "2026-07-31T08:00:00Z",
      artifacts: [],
      rows: [],
      conflicts: [],
    });

    renderPage();

    expect(await screen.findByText("暂无已确认数据")).toBeInTheDocument();
    expect(await screen.findByText("有 1 个批次等待确认")).toBeInTheDocument();
    expect(screen.queryByText("已有可用数据")).not.toBeInTheDocument();
  });

  it("loads history details only when the operator opens one batch", async () => {
    vi.mocked(listAccountDataImports).mockResolvedValueOnce({
      items: [
        {
          id: 81,
          status: "committed",
          source_kind: "platform_export",
          template_code: "douyin_work_list_v1",
          row_count: 30,
          period_start: "2026-07-01",
          period_end: "2026-07-31",
          committed_at: "2026-07-31T08:20:00Z",
          revoked_at: null,
          created_by_id: 1,
          created_by_name: "Operator",
          created_at: "2026-07-31T08:00:00Z",
        },
      ],
    });
    vi.mocked(getAccountDataImportBatch).mockResolvedValueOnce({
      id: 81,
      status: "committed",
      source_kind: "platform_export",
      template_code: "douyin_work_list_v1",
      row_count: 30,
      period_start: "2026-07-01",
      period_end: "2026-07-31",
      committed_at: "2026-07-31T08:20:00Z",
      revoked_at: null,
      created_by_id: 1,
      created_by_name: "Operator",
      created_at: "2026-07-31T08:00:00Z",
      artifacts: [],
      rows: [],
      conflicts: [],
    });
    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "导入记录" }));
    expect(getAccountDataImportBatch).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "查看批次 81" }));

    await waitFor(() =>
      expect(getAccountDataImportBatch).toHaveBeenCalledWith(42, 81),
    );
  });

  it("remounts the data workspace and clears the import queue when switching accounts", async () => {
    vi.mocked(createAccountDataImportJob).mockImplementation(async (accountId) =>
      buildImportJob(accountId),
    );
    renderSwitcher();
    fireEvent.click(await screen.findByRole("tab", { name: "导入与补录" }));
    fireEvent.change(screen.getByTestId("account-data-file-input"), {
      target: { files: [new File(["daily"], "播放数据.xlsx")] },
    });
    expect(await screen.findByText("播放数据.xlsx")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "切换账号" }));

    expect(await screen.findByText("账号 99")).toBeInTheDocument();
    expect(screen.queryByText("播放数据.xlsx")).not.toBeInTheDocument();
    expect(getAccountDataStatus).toHaveBeenCalledWith(99);
  });
});
